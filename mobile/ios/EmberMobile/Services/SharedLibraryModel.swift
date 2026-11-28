import Foundation
import Combine
import CryptoKit

/// Ember-owned playlists against the backend, with offline edits.
///
/// Every edit is applied optimistically to the cached copies and appended to
/// an ordered queue before any network work; the queue is replayed in order
/// once the server is reachable again. A 409 mid-replay means another device
/// moved the playlist ahead: deletes re-issue against the fresh revision,
/// updates are dropped rather than silently overwriting the other device.
@MainActor
final class SharedLibraryModel: ObservableObject {
    @Published private(set) var playlists: [SharedPlaylist] = []
    @Published private(set) var details: [String: SharedPlaylist] = [:]
    @Published private(set) var queue: [LibraryMutation] = []
    @Published private(set) var isOnline = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var isSaving = false
    @Published private(set) var loadingDetails: Set<String> = []
    @Published private(set) var errorMessage: String?

    private var api: APIClient?
    private var epoch = UUID()
    private var cacheURL: URL?
    private let cacheDirectory: URL?

    /// The oldest queued mutation: the exact record a relaunch retry reuses.
    var pending: LibraryMutation? { queue.first }

    /// Edits work offline too; they queue behind the pending ones and replay
    /// in order. Only a cache that could not be opened blocks editing, because
    /// a queued edit that cannot survive a relaunch would pretend to be saved.
    var canEdit: Bool { cacheURL != nil && !isSaving }

    init(cacheDirectory: URL? = nil) {
        self.cacheDirectory = cacheDirectory
    }

    func configure(_ api: APIClient?) {
        epoch = UUID()
        self.api = api
        playlists = []
        details = [:]
        queue = []
        isOnline = false
        isRefreshing = false
        isSaving = false
        loadingDetails = []
        errorMessage = nil
        cacheURL = nil
        guard let api else { return }
        do {
            let directory: URL
            if let cacheDirectory {
                directory = cacheDirectory
            } else {
                directory = try FileManager.default.url(for: .applicationSupportDirectory,
                    in: .userDomainMask, appropriateFor: nil, create: true)
                    .appendingPathComponent("Ember/SharedLibrary", isDirectory: true)
            }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let digest = SHA256.hash(data: Data(api.baseURL.absoluteString.utf8))
                .map { String(format: "%02x", $0) }.joined()
            let url = directory.appendingPathComponent("\(digest).json")
            cacheURL = url
            if FileManager.default.fileExists(atPath: url.path) {
                let state = try JSONDecoder().decode(DiskState.self, from: Data(contentsOf: url))
                playlists = state.playlists
                details = state.details
                // A cache written before queued edits carries one pending save.
                queue = state.queue ?? (state.pending.map { [$0] } ?? [])
            }
        } catch {
            // Never discard an unreadable pending save and then allow fresh edits.
            cacheURL = nil
            errorMessage = "The private playlist cache could not be opened. Reconnect after checking device storage."
        }
    }

    func refresh() async {
        guard let api, !isRefreshing, !isSaving else { return }
        let ticket = epoch
        isRefreshing = true
        defer { if epoch == ticket { isRefreshing = false } }
        do {
            let result = try await api.libraryPlaylists()
            guard epoch == ticket else { return }
            playlists = mergeServerList(result)
            let ids = Set(result.map(\.id))
            let queuedCreates = Set(queue.filter { $0.operation == .create }.map(\.playlistID))
            details = details.filter { ids.contains($0.key) || queuedCreates.contains($0.key) }
            isOnline = true
            errorMessage = nil
            try saveCache()
            await replayQueue()
        } catch {
            guard epoch == ticket else { return }
            isOnline = false
            if case APIError.server(404, _, _) = error {
                errorMessage = "Shared playlists need Ember backend 0.3.0 or newer. Update the laptop server."
            } else {
                errorMessage = error.localizedDescription
            }
        }
    }

    @discardableResult
    func load(_ id: String) async -> SharedPlaylist? {
        guard let api, !loadingDetails.contains(id), !isSaving else { return details[id] }
        let ticket = epoch
        loadingDetails.insert(id)
        defer { if epoch == ticket { loadingDetails.remove(id) } }
        do {
            let result = try await api.libraryPlaylist(id: id)
            guard epoch == ticket else { return nil }
            details[id] = result
            upsertSummary(result)
            isOnline = true
            errorMessage = nil
            try saveCache()
            await replayQueue()
            return result
        } catch {
            guard epoch == ticket else { return nil }
            isOnline = false
            errorMessage = error.localizedDescription
            if case APIError.server(404, _, _) = error { details.removeValue(forKey: id) }
            return nil
        }
    }

    /// Applies the edit to the cached copies immediately, queues the exact
    /// mutation, and replays the queue when the server is reachable. Returning
    /// true online means the server acknowledged it; offline it means queued.
    @discardableResult
    func submit(_ mutation: LibraryMutation) async -> Bool {
        guard canEdit else { return false }
        guard queue.count < 50 else {
            errorMessage = "Too many unsynced edits. Reconnect to the server and let them sync."
            return false
        }
        let previousPlaylists = playlists
        let previousDetails = details
        applyOptimistically(mutation)
        queue.append(mutation)
        do {
            // Persist the exact request before sending; timeout/relaunch retries keep IDs.
            try saveCache()
        } catch {
            playlists = previousPlaylists
            details = previousDetails
            errorMessage = "Could not save the pending edit. Free device storage before editing."
            return false
        }
        guard isOnline else { return true }
        return await replayQueue()
    }

    func retryPending() async -> Bool {
        await replayQueue()
    }

    /// Sends the queued edits oldest-first. Every exit either drains the
    /// queue or leaves it exactly as the retry records require: an ambiguous
    /// failure keeps the queue verbatim (the same request IDs retry), a hard
    /// reject drops only the rejected edit, and a 409 resolves the conflict
    /// instead of silently overwriting the other device's changes.
    private func replayQueue() async -> Bool {
        guard let api, !queue.isEmpty, !isSaving else { return queue.isEmpty }
        epoch = UUID() // Ignore reads that began before this replay.
        let ticket = epoch
        isRefreshing = false
        loadingDetails = []
        isSaving = true
        errorMessage = nil
        var touched: Set<String> = []
        var conflictAttempts: [String: Int] = [:]
        var ok = true
        var dropped = false // A mutation left without server acknowledgment.
        while !queue.isEmpty, epoch == ticket {
            let mutation = queue[0]
            touched.insert(mutation.playlistID)
            do {
                let receipt = try await api.mutateLibrary(mutation)
                guard epoch == ticket else { return false }
                queue.removeFirst()
                isOnline = true // A successful authenticated call proves reachability.
                applyReceipt(mutation, receipt)
                persistQueueAfterNetwork(mutation)
            } catch {
                guard epoch == ticket else { return false }
                switch error {
                case APIError.server(409, _, _):
                    conflictAttempts[mutation.playlistID, default: 0] += 1
                    if conflictAttempts[mutation.playlistID]! <= 2 {
                        let resolved = await resolveConflict(mutation)
                        if resolved { continue } // Retry the same slot against the fresh revision.
                    }
                    // Unmergeable: discard this playlist's queued edits and show
                    // the server's copy rather than overwrite the other device.
                    discardPlaylistQueue(mutation.playlistID)
                    dropped = true
                    errorMessage = "This playlist changed on another device while you were offline. It was reloaded; apply your remaining edits again."
                case APIError.server(let status, _, _) where [400, 401, 413, 415].contains(status):
                    queue.removeFirst()
                    dropped = true
                    persistQueueAfterNetwork(mutation)
                    errorMessage = error.localizedDescription
                case APIError.server(404, _, _):
                    // The playlist no longer exists; nothing queued against it can apply.
                    discardPlaylistQueue(mutation.playlistID)
                    dropped = true
                    errorMessage = "That playlist no longer exists on the server. Its queued edits were dropped."
                case APIError.invalidConfiguration:
                    queue.removeFirst()
                    dropped = true
                    persistQueueAfterNetwork(mutation)
                    errorMessage = error.localizedDescription
                default:
                    // Transport, malformed success and 5xx are ambiguous: keep the queue.
                    ok = false
                    errorMessage = "Edits not confirmed yet. They will sync when the server is reachable. " + error.localizedDescription
                }
                break
            }
        }
        isSaving = false
        guard epoch == ticket else { return false }
        if queue.isEmpty, isOnline {
            await refresh()
            for id in touched.sorted() where details[id] != nil {
                _ = await load(id)
            }
        }
        // True only when every queued edit was acknowledged; a dropped edit
        // (conflict, reject, gone playlist) was never saved on the server.
        return ok && queue.isEmpty && !dropped
    }

    /// A 409 means the server revision moved. Deletes can safely re-issue
    /// against the fresh revision. Anything else is left to the caller's
    /// conservative discard: an update built from stale data must never
    /// overwrite what another device saved.
    private func resolveConflict(_ mutation: LibraryMutation) async -> Bool {
        guard let api else { return false }
        do {
            let fresh = try await api.libraryPlaylist(id: mutation.playlistID)
            if case .delete = mutation.operation {
                // A delete re-issues against whatever the server now holds.
                queue[0] = .delete(fresh)
                return true
            }
            details[mutation.playlistID] = fresh
            upsertSummary(fresh)
            return false
        } catch {
            // Server unreachable or the playlist is gone: let the caller drop.
            return false
        }
    }

    private func discardPlaylistQueue(_ playlistID: String) {
        queue.removeAll { $0.playlistID == playlistID }
        persistQueueAfterNetwork(nil)
    }

    /// Records acknowledged revisions in the cached copies so the next edit
    /// bases its expectedRevision on what the server actually holds.
    private func applyReceipt(_ mutation: LibraryMutation, _ receipt: LibraryReceipt) {
        switch mutation.operation {
        case .delete:
            playlists.removeAll { $0.id == mutation.playlistID }
            details.removeValue(forKey: mutation.playlistID)
        case .create, .update:
            guard var playlist = details[mutation.playlistID] ?? playlists.first(where: { $0.id == mutation.playlistID }) else { return }
            playlist = SharedPlaylist(id: playlist.id, name: playlist.name,
                revision: receipt.revision, createdAt: playlist.createdAt,
                updatedAt: playlist.updatedAt, trackCount: playlist.trackCount,
                entries: playlist.entries)
            upsertSummary(playlist)
            details[mutation.playlistID] = playlist
        }
    }

    private func applyOptimistically(_ mutation: LibraryMutation) {
        switch mutation.operation {
        case .create:
            guard let id = mutation.body.id, let name = mutation.body.name else { return }
            let entries = mutation.body.entries ?? []
            // The server assigns revision 1 to a fresh playlist; base queued
            // edits on that so their expectedRevision matches after the create.
            let playlist = SharedPlaylist(id: id, name: name, revision: 1,
                createdAt: nowString(), updatedAt: nowString(),
                trackCount: entries.count, entries: entries)
            playlists.append(playlist)
            details[id] = playlist
        case .update:
            guard let playlist = details[mutation.playlistID]
                ?? playlists.first(where: { $0.id == mutation.playlistID }) else { return }
            let entries = mutation.body.entries ?? playlist.entries ?? []
            let updated = SharedPlaylist(id: playlist.id,
                name: mutation.body.name ?? playlist.name,
                revision: playlist.revision + 1, createdAt: playlist.createdAt,
                updatedAt: nowString(), trackCount: entries.count, entries: entries)
            upsertSummary(updated)
            details[mutation.playlistID] = updated
        case .delete:
            playlists.removeAll { $0.id == mutation.playlistID }
            details.removeValue(forKey: mutation.playlistID)
        }
    }

    private func upsertSummary(_ playlist: SharedPlaylist) {
        if let index = playlists.firstIndex(where: { $0.id == playlist.id }) {
            playlists[index] = SharedPlaylist(id: playlist.id, name: playlist.name,
                revision: playlist.revision, createdAt: playlist.createdAt,
                updatedAt: playlist.updatedAt, trackCount: playlist.trackCount, entries: nil)
        } else {
            playlists.append(SharedPlaylist(id: playlist.id, name: playlist.name,
                revision: playlist.revision, createdAt: playlist.createdAt,
                updatedAt: playlist.updatedAt, trackCount: playlist.trackCount, entries: nil))
        }
    }

    /// The server list wins for playlists it still holds; a create that only
    /// exists locally stays visible until its create is acknowledged.
    private func mergeServerList(_ result: [SharedPlaylist]) -> [SharedPlaylist] {
        var merged = result
        // A playlist whose create is still queued exists only locally.
        for local in playlists
        where !result.contains(where: { $0.id == local.id })
            && queue.contains(where: { $0.playlistID == local.id && $0.operation == .create }) {
            merged.append(local)
        }
        return merged
    }

    /// A failed cache rewrite after a network change must keep the queue:
    /// losing the retry record would drop edits the server may have applied.
    private func persistQueueAfterNetwork(_ mutation: LibraryMutation?) {
        do {
            try saveCache()
        } catch {
            if queue.isEmpty, let mutation { queue = [mutation] }
            errorMessage = "Saved on the server, but the pending edits could not be recorded on disk. Free device storage, then retry."
        }
    }

    private func nowString() -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: Date())
    }

    private struct DiskState: Codable {
        let playlists: [SharedPlaylist]
        let details: [String: SharedPlaylist]
        let queue: [LibraryMutation]?
        let pending: LibraryMutation?
    }

    private func saveCache() throws {
        guard var url = cacheURL else { throw CocoaError(.fileWriteNoPermission) }
        let data = try JSONEncoder().encode(
            DiskState(playlists: playlists, details: details, queue: queue, pending: queue.first))
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: url, options: .atomic)
        #endif
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try url.setResourceValues(values)
    }
}
