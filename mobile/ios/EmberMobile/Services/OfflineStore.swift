import Foundation
import Combine

/// A downloaded Ember playlist: the video IDs it pins and the revision it was
/// built from. Audio files are keyed by `track.id`, so one file can serve
/// several playlists and a revision bump never invalidates audio.
struct OfflinePlaylistState: Codable, Equatable {
    var trackIDs: [String]
    var revision: Int
    var complete: Bool
}

struct OfflineDownloadProgress: Equatable {
    let playlistID: String
    let done: Int
    let total: Int
}

/// Offline audio for Ember playlists, in Application Support (never Caches, so
/// iOS cannot purge it) with a bounded total size. Files not in any manifest
/// are deleted; files in a manifest are pinned and never removed silently.
/// A download that would exceed the cap stops and leaves a partial manifest.
@MainActor
final class OfflineStore: ObservableObject {
    static let byteCap: Int64 = 2 * 1024 * 1024 * 1024
    static let minimumFreeBytes: Int64 = 256 * 1024 * 1024
    /// One request about every two seconds keeps a bulk download well under the
    /// server's 60-requests-per-minute client limit alongside library traffic.
    static let pacingNanoseconds: UInt64 = 2_000_000_000
    static let rateLimitRetryNanoseconds: UInt64 = 30_000_000_000
    static let rateLimitRetries = 3

    @Published private(set) var playlists: [String: OfflinePlaylistState] = [:]
    @Published private(set) var progress: OfflineDownloadProgress?
    @Published private(set) var activePlaylistID: String?
    @Published private(set) var notice: String?
    /// Which playlist the current `notice` belongs to. A notice outlives its
    /// run, so the view must not key on `activePlaylistID`.
    @Published private(set) var noticePlaylistID: String?

    let directory: URL
    private let indexURL: URL
    private var downloadTask: Task<Void, Never>?

    /// `directory` is injectable so the macOS check can run against a temp path.
    init(directory: URL? = nil) {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        self.directory = directory
            ?? support.appendingPathComponent("Ember", isDirectory: true)
                .appendingPathComponent("Offline", isDirectory: true)
        indexURL = self.directory.appendingPathComponent("index.json")
        makeDirectory()
        loadIndex()
        // No download can be running at init, so anything not pinned now is
        // orphaned audio from a previous crash or removal.
        sweepUnpinned()
    }

    static func isTrackID(_ value: String) -> Bool {
        value.count == 11 && value.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
    }

    // MARK: Reading

    /// Returns the audio file only when it exists on disk with a plausible size;
    /// a missing or truncated file means the track is not really downloaded.
    func fileURL(for trackID: String) -> URL? {
        guard Self.isTrackID(trackID) else { return nil }
        let url = directory.appendingPathComponent("\(trackID).m4a")
        guard let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize,
              size > 0, size <= 48 * 1024 * 1024 else { return nil }
        return url
    }

    func contains(_ trackID: String) -> Bool { fileURL(for: trackID) != nil }

    func pinnedTrackIDs() -> Set<String> {
        Set(playlists.values.flatMap(\.trackIDs))
    }

    func bytesUsed() -> Int64 {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.fileSizeKey]
        )) ?? []
        return files
            .filter { $0.pathExtension == "m4a" }
            .reduce(0) { $0 + Int64((try? $1.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
    }

    func state(for playlistID: String) -> OfflinePlaylistState? { playlists[playlistID] }

    var isDownloading: Bool { activePlaylistID != nil }

    /// One user-facing line for the playlist detail screen.
    func statusLine(for playlistID: String) -> String? {
        if activePlaylistID == playlistID, let progress, progress.total > 0 {
            return "Downloading \(progress.done) of \(progress.total)…"
        }
        guard let state = playlists[playlistID] else { return nil }
        let present = state.trackIDs.filter { contains($0) }.count
        if state.complete {
            return "Downloaded \u{00b7} \(present) songs \u{00b7} \(Self.byteText(storedBytes(of: state.trackIDs)))"
        }
        if present == 0 {
            return "Download incomplete \u{2014} nothing stored yet."
        }
        return "Partially downloaded \u{00b7} \(present) of \(state.trackIDs.count) songs"
    }

    // MARK: Manifest maintenance

    /// Refreshes one downloaded playlist's manifest after an online library
    /// load. Adds newly appearing video IDs, drops (and deletes, unless another
    /// playlist still pins them) removed ones, and records the new revision.
    /// Playlists never downloaded are left alone.
    @discardableResult
    func updateManifest(for playlist: SharedPlaylist) throws -> Bool {
        guard var state = playlists[playlist.id], playlist.entries != nil else { return false }
        let target = orderedTrackIDs(playlist)
        guard !target.isEmpty else {
            try remove(playlistID: playlist.id)
            return true
        }
        let removed = Set(state.trackIDs).subtracting(target)
        state.trackIDs = target
        state.revision = playlist.revision
        playlists[playlist.id] = state
        for id in removed where !pinnedTrackIDs().contains(id) {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent("\(id).m4a"))
        }
        state.complete = complete(state)
        playlists[playlist.id] = state
        try save()
        objectWillChange.send()
        return true
    }

    func remove(playlistID: String) throws {
        if activePlaylistID == playlistID {
            cancelDownload()
        }
        guard let state = playlists.removeValue(forKey: playlistID) else { return }
        let stillPinned = pinnedTrackIDs()
        for id in state.trackIDs where !stillPinned.contains(id) {
            try? FileManager.default.removeItem(at: directory.appendingPathComponent("\(id).m4a"))
        }
        try save()
        objectWillChange.send()
    }

    func removeAll() throws {
        let ids = Array(playlists.keys)
        for id in ids {
            try remove(playlistID: id)
        }
    }

    // MARK: Downloading

    func cancelDownload() {
        downloadTask?.cancel()
        downloadTask = nil
    }

    /// Downloads every missing audio file for one playlist, sequentially and
    /// paced. Each finished file is durable before the next starts, so an
    /// interrupted run resumes where it stopped. Cancelling or hitting the cap
    /// leaves the manifest marked incomplete. Caller cancellation is not
    /// propagated — only `cancelDownload()` stops a running download.
    func download(playlist: SharedPlaylist, api: APIClient) async {
        guard activePlaylistID == nil, downloadTask == nil else { return }
        let tracks = orderedTracks(playlist)
        guard !tracks.isEmpty else { return }
        let ids = tracks.map(\.id)
        var state = playlists[playlist.id]
            ?? OfflinePlaylistState(trackIDs: ids, revision: playlist.revision, complete: false)
        state.trackIDs = ids
        state.revision = playlist.revision
        playlists[playlist.id] = state
        try? save()
        activePlaylistID = playlist.id
        notice = nil
        noticePlaylistID = playlist.id
        let task = Task { [weak self] in
            guard let self else { return }
            await self.runDownload(playlistID: playlist.id, tracks: tracks, api: api)
        }
        downloadTask = task
        await task.value
        downloadTask = nil
    }

    private func runDownload(
        playlistID: String,
        tracks: [Track],
        api: APIClient
    ) async {
        defer { activePlaylistID = nil }
        let ids = tracks.map(\.id)
        let missing = tracks.filter { fileURL(for: $0.id) == nil }
        progress = OfflineDownloadProgress(
            playlistID: playlistID, done: ids.count - missing.count, total: ids.count
        )
        var unavailable = 0
        for (index, track) in missing.enumerated() {
            if Task.isCancelled {
                notice = "Download stopped at \(done(of: ids)) of \(ids.count)."
                syncCompletion(playlistID: playlistID)
                progress = nil
                return
            }
            guard Self.byteCap - bytesUsed() > 48 * 1024 * 1024 else {
                // A file can be as large as one fetch, so keep room for it
                // rather than overshooting the cap.
                notice = "Offline storage is full at \(Self.byteText(bytesUsed())). Remove downloads to make room."
                syncCompletion(playlistID: playlistID)
                progress = nil
                return
            }
            guard freeSpace() > Self.minimumFreeBytes else {
                notice = "The iPhone is low on storage. Download stopped at \(done(of: ids)) of \(ids.count)."
                syncCompletion(playlistID: playlistID)
                progress = nil
                return
            }
            var lastError: APIError?
            var retryAttempts = 0
            while retryAttempts <= Self.rateLimitRetries {
                do {
                    _ = try await api.fetchAudio(for: track, into: directory)
                    lastError = nil
                    break
                } catch let error as APIError {
                    if case .server(429, _, _) = error, retryAttempts < Self.rateLimitRetries {
                        retryAttempts += 1
                        try? await Task.sleep(nanoseconds: Self.rateLimitRetryNanoseconds)
                        if Task.isCancelled { break }
                        continue
                    }
                    // One unavailable recording must not block the playlist:
                    // skip it and keep downloading the rest. Only server-wide
                    // failures stop the run.
                    switch error {
                    case .fileTooLarge, .invalidResponse, .server(404, _, _):
                        unavailable += 1
                    default:
                        lastError = error
                    }
                    break
                } catch {
                    lastError = .transport("Could not reach the Ember server.")
                    break
                }
            }
            if Task.isCancelled {
                notice = "Download stopped at \(done(of: ids)) of \(ids.count)."
                syncCompletion(playlistID: playlistID)
                progress = nil
                return
            }
            if let error = lastError {
                notice = "Download stopped at \(done(of: ids)) of \(ids.count): \(error.errorDescription ?? "failed")"
                syncCompletion(playlistID: playlistID)
                progress = nil
                return
            }
            progress = OfflineDownloadProgress(
                playlistID: playlistID, done: ids.count - missing.count + index + 1, total: ids.count
            )
            if index + 1 < missing.count {
                try? await Task.sleep(nanoseconds: Self.pacingNanoseconds)
            }
        }
        notice = completeMessage(playlistID: playlistID, total: ids.count, unavailable: unavailable)
        syncCompletion(playlistID: playlistID)
        progress = nil
        sweepUnpinned()
    }

    /// Deletes audio no manifest pins: crashes and races with removal can
    /// leave files behind that would otherwise count against the cap forever.
    private func sweepUnpinned() {
        let pinned = pinnedTrackIDs()
        let files = (try? FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: nil
        )) ?? []
        for file in files where file.pathExtension == "m4a" {
            let id = file.deletingPathExtension().lastPathComponent
            if !pinned.contains(id) {
                try? FileManager.default.removeItem(at: file)
            }
        }
    }

    private func done(of ids: [String]) -> Int {
        ids.filter { contains($0) }.count
    }

    private func storedBytes(of ids: [String]) -> Int64 {
        ids.reduce(Int64(0)) { total, id in
            total + Int64((try? directory.appendingPathComponent("\(id).m4a")
                .resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
    }

    private func syncCompletion(playlistID: String) {
        guard var state = playlists[playlistID] else { return }
        state.complete = complete(state)
        playlists[playlistID] = state
        try? save()
        objectWillChange.send()
    }

    private func complete(_ state: OfflinePlaylistState) -> Bool {
        state.trackIDs.allSatisfy { contains($0) }
    }

    private func completeMessage(playlistID: String, total: Int, unavailable: Int) -> String {
        guard total > 0 else { return "Download finished." }
        let suffix = unavailable > 0 ? " · \(unavailable) unavailable on YouTube" : ""
        // Counts use the run-start snapshot; bytes reflect what is on disk now.
        let bytes = playlists[playlistID].map { storedBytes(of: $0.trackIDs) } ?? 0
        return "Downloaded \(max(0, total - unavailable)) of \(total) songs (\(Self.byteText(bytes)))\(suffix)."
    }

    private func freeSpace() -> Int64 {
        let values = try? directory.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        return values?.volumeAvailableCapacityForImportantUsage ?? Int64.max
    }

    private func orderedTracks(_ playlist: SharedPlaylist) -> [Track] {
        var seen = Set<String>()
        var tracks: [Track] = []
        for entry in playlist.entries ?? [] where !seen.contains(entry.track.id) {
            guard Self.isTrackID(entry.track.id) else { continue }
            seen.insert(entry.track.id)
            tracks.append(entry.track)
        }
        return tracks
    }

    private func orderedTrackIDs(_ playlist: SharedPlaylist) -> [String] {
        orderedTracks(playlist).map(\.id)
    }

    // MARK: Persistence

    static func byteText(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }

    private func makeDirectory() {
        let manager = FileManager.default
        try? manager.createDirectory(at: directory, withIntermediateDirectories: true)
        // A crash can leave .part files behind; they are never valid audio.
        // No download can be running at init, so sweeping here is safe.
        if let leftovers = try? manager.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) {
            for file in leftovers where file.pathExtension == "part" {
                try? manager.removeItem(at: file)
            }
        }
        if let values = try? directory.resourceValues(forKeys: [.isExcludedFromBackupKey]),
           values.isExcludedFromBackup != true {
            var excluded = URLResourceValues()
            excluded.isExcludedFromBackup = true
            var marked = directory
            try? marked.setResourceValues(excluded)
        }
    }

    private func loadIndex() {
        guard let data = try? Data(contentsOf: indexURL),
              let index = try? JSONDecoder().decode([String: OfflinePlaylistState].self, from: data)
        else { return }
        playlists = index.filter { key, value in
            !key.isEmpty && value.trackIDs.allSatisfy(Self.isTrackID)
        }
    }

    private func save() throws {
        let data = try JSONEncoder().encode(playlists)
        #if os(iOS)
        try data.write(to: indexURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: indexURL, options: .atomic)
        #endif
    }
}
