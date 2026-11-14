import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    enum Sheet: String, Identifiable {
        case nowPlaying
        case settings

        var id: String { rawValue }
    }

    @Published var serverURL: String
    @Published var accessKey: String
    @Published var searchText = ""
    @Published private(set) var results: [Track] = []
    @Published private(set) var isSearching = false
    @Published private(set) var isConnecting = false
    @Published private(set) var isRestoring = true
    @Published private(set) var connectionError: String?
    @Published private(set) var errorMessage: String?
    @Published private(set) var radioTrackID: String?
    @Published private(set) var preparingTrackID: String?
    @Published private(set) var preparingEntryID: UUID?
    @Published private(set) var queue = PlaybackQueue()
    @Published private(set) var savedPlaylists: [SavedPlaylist] = []
    @Published var activeSheet: Sheet?

    let player = PlayerController()

    @Published private(set) var client: APIClient? = nil
    private var radioTask: Task<Void, Never>?
    private var radioToken = UUID()
    private var searchTask: Task<Void, Never>?
    private var preparationTask: Task<Void, Never>?
    private var prefetchTask: Task<Void, Never>?
    private var playbackGate = PlaybackGate()
    private var playingEntryID: UUID?
    private var didRestore = false

    private static let serverURLKey = "ember.server.url"

    init() {
        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? ""
        accessKey = ""
        savedPlaylists = PlaylistStore.load()
        player.lastFM.client = { [weak self] in self?.client }
        player.onTrackEnded = { [weak self] in
            self?.advanceQueueAfterEnd()
        }
        player.onPlaybackFailed = { [weak self] message in
            self?.handlePlaybackFailure(message)
        }
        player.onNext = { [weak self] in
            self?.playNextTrack()
        }
        player.onPrevious = { [weak self] in
            self?.playPreviousTrack()
        }
        player.onPauseRequested = { [weak self] in
            self?.cancelPreparation()
        }
        player.isPreparingPlayback = { [weak self] in
            self?.preparingEntryID != nil
        }
        player.onResumeRequested = { [weak self] in
            guard let self, let entry = self.queue.current else { return false }
            if self.preparingEntryID != nil { return true }
            guard self.playingEntryID != entry.id || self.player.state == .failed else { return false }
            self.beginPlayback(entry)
            return true
        }
    }

    var isConnected: Bool {
        client != nil
    }

    var currentQueueEntry: QueueEntry? {
        queue.current
    }

    var isCurrentTrackLoaded: Bool {
        queue.currentID != nil && playingEntryID == queue.currentID
    }

    var isPlaybackActive: Bool {
        preparingEntryID != nil || (isCurrentTrackLoaded && (player.state == .playing || player.state == .loading))
    }

    func restoreSession() async {
        guard !didRestore else { return }
        didRestore = true
        defer { isRestoring = false }

        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? ""
        accessKey = KeychainStore.read() ?? ""
        guard !serverURL.isEmpty, !accessKey.isEmpty else { return }

        isConnecting = true
        defer { isConnecting = false }
        do {
            let url = try normalizedServerURL(serverURL)
            let api = APIClient(baseURL: url, token: accessKey)
            try await api.health()
            client = api
            connectionError = nil
        } catch {
            client = nil
            connectionError = "Could not reconnect to the saved server. Check Tailscale, then try again."
        }
    }

    func connect() async {
        guard !isConnecting else { return }
        connectionError = nil
        errorMessage = nil
        isConnecting = true
        defer { isConnecting = false }

        let key = accessKey.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let url = try normalizedServerURL(serverURL)
            guard !key.isEmpty else {
                throw APIError.invalidConfiguration("Enter the access key from the server's .env file.")
            }
            let api = APIClient(baseURL: url, token: key)
            try await api.health()
            try KeychainStore.save(key)
            UserDefaults.standard.set(url.absoluteString, forKey: Self.serverURLKey)
            serverURL = url.absoluteString
            accessKey = key
            client = api
            connectionError = nil
        } catch {
            connectionError = (error as? LocalizedError)?.errorDescription
                ?? "Could not connect to the server."
        }
    }

    func disconnect() {
        cancelRadio()
        searchTask?.cancel()
        searchTask = nil
        preparationTask?.cancel()
        preparationTask = nil
        prefetchTask?.cancel()
        prefetchTask = nil
        playbackGate.next()
        preparingTrackID = nil
        preparingEntryID = nil
        player.stop()
        playingEntryID = nil
        queue.removeAll()
        client = nil
        results = []
        searchText = ""
        isSearching = false
        errorMessage = nil
        connectionError = nil
        KeychainStore.delete()
        accessKey = ""
    }

    func scheduleSearch() {
        searchTask?.cancel()
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.count >= 2 else {
            results = []
            isSearching = false
            return
        }
        isSearching = true
        searchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await self?.performSearch(query)
        }
    }

    func searchNow() {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.count >= 2 else { return }
        searchTask?.cancel()
        searchTask = Task { await performSearch(query) }
    }

    private func cancelRadio() {
        radioToken = UUID()
        radioTask?.cancel()
        radioTask = nil
        radioTrackID = nil
    }

    func startSongRadio(_ seed: Track) {
        guard let api = client else { return }
        cancelRadio()
        let token = radioToken
        radioTrackID = seed.id
        errorMessage = nil
        radioTask = Task { [weak self] in
            do {
                let recommendations = try await api.radio(for: seed)
                guard let self, !Task.isCancelled, self.radioToken == token else { return }
                self.radioTask = nil
                self.radioTrackID = nil
                var seen: Set<String> = [seed.id]
                let related = recommendations.filter { seen.insert($0.id).inserted }
                guard !related.isEmpty else {
                    self.errorMessage = "No matching Last.fm recommendations were found. Your queue is unchanged."
                    return
                }
                self.play(tracks: [seed] + related, startingAt: 0)
            } catch {
                guard let self, !Task.isCancelled, self.radioToken == token else { return }
                self.radioTask = nil
                self.radioTrackID = nil
                self.errorMessage = "Song radio unavailable. \(error.localizedDescription) Your queue is unchanged."
            }
        }
    }

    func play(_ track: Track) {
        guard client != nil else { return }
        let entry = queue.start(track: track)
        beginPlayback(entry)
    }

    func play(tracks: [Track], startingAt index: Int) {
        guard client != nil, !tracks.isEmpty else { return }
        guard let entry = queue.replace(tracks: tracks, startingAt: index) else { return }
        beginPlayback(entry)
    }

    func playAll(_ tracks: [Track]) {
        play(tracks: tracks, startingAt: 0)
    }

    func shufflePlay(_ tracks: [Track]) {
        play(tracks: tracks.shuffled(), startingAt: 0)
    }

    func playNext(_ track: Track) {
        guard client != nil else { return }
        let wasIdle = queue.currentID == nil
        let entry = queue.playNext(track)
        if wasIdle {
            beginPlayback(entry)
        } else {
            restartPrefetch()
        }
    }

    func addToQueue(_ track: Track) {
        guard client != nil else { return }
        let wasIdle = queue.currentID == nil
        let entry = queue.append(track)
        if wasIdle {
            beginPlayback(entry)
        } else {
            restartPrefetch()
        }
    }

    func playQueueEntry(_ entry: QueueEntry) {
        guard client != nil, let selected = queue.select(id: entry.id) else { return }
        beginPlayback(selected)
    }

    func playNextTrack() {
        guard let entry = queue.advanceAfterEnd() else { return }
        beginPlayback(entry)
    }

    func playPreviousTrack() {
        if isCurrentTrackLoaded, player.currentTime > 4 {
            player.seek(to: 0)
            return
        }
        guard let entry = queue.previous() else {
            player.seek(to: 0)
            return
        }
        beginPlayback(entry)
    }

    func removeUpcoming(_ entry: QueueEntry) {
        if queue.removeUpcoming(id: entry.id) {
            restartPrefetch()
        }
    }

    func moveUpcoming(_ entry: QueueEntry, offset: Int) {
        if queue.moveUpcoming(id: entry.id, offset: offset) {
            restartPrefetch()
        }
    }

    func clearUpcoming() {
        queue.clearUpcoming()
        restartPrefetch()
    }

    func addPlaylist(_ summary: PlaylistSummary) {
        let saved = SavedPlaylist(summary: summary)
        var updated = savedPlaylists.filter { $0.id != saved.id }
        updated.insert(saved, at: 0)
        updated = Array(updated.prefix(200))
        do {
            try PlaylistStore.save(updated)
            savedPlaylists = updated
        } catch {
            errorMessage = "Could not save this playlist on the iPhone."
        }
    }

    func removePlaylist(_ playlist: SavedPlaylist) {
        let updated = savedPlaylists.filter { $0.id != playlist.id }
        do {
            try PlaylistStore.save(updated)
            savedPlaylists = updated
        } catch {
            errorMessage = "Could not update the saved playlist library."
        }
    }

    func isSavedPlaylist(_ summary: PlaylistSummary) -> Bool {
        savedPlaylists.contains { $0.id == summary.id }
    }

    func previewPlaylist(url: String) async throws -> PlaylistPage {
        guard let api = client else {
            throw APIError.invalidConfiguration("Connect to your Ember server first.")
        }
        let value = url.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else {
            throw APIError.invalidConfiguration("Paste a public or unlisted YouTube Music playlist link.")
        }
        return try await api.previewPlaylist(url: value)
    }

    func playlistPage(id: String, offset: Int, limit: Int = 100) async throws -> PlaylistPage {
        guard let api = client else {
            throw APIError.invalidConfiguration("Connect to your Ember server first.")
        }
        return try await api.playlistPage(playlistID: id, offset: offset, limit: limit)
    }

    func lyrics(for track: Track) async throws -> LyricsResponse {
        guard let api = client else {
            throw APIError.invalidConfiguration("Connect to your Ember server first.")
        }
        return try await api.lyrics(for: track)
    }

    func clearError() {
        errorMessage = nil
    }

    private func beginPlayback(_ entry: QueueEntry) {
        cancelRadio()
        guard let api = client else { return }
        player.pause()
        playingEntryID = nil
        preparationTask?.cancel()
        prefetchTask?.cancel()
        prefetchTask = nil
        let token = playbackGate.next()
        preparingTrackID = entry.track.id
        preparingEntryID = entry.id
        errorMessage = nil

        let protected = Set([player.track?.id, Optional(entry.track.id)].compactMap { $0 })
        preparationTask = Task { [weak self] in
            guard let self else { return }
            do {
                let fileURL = try await api.downloadAudio(
                    for: entry.track,
                    protectingTrackIDs: protected
                )
                guard !Task.isCancelled,
                      self.playbackGate.accepts(token),
                      self.queue.currentID == entry.id,
                      self.client != nil else {
                    return
                }
                self.preparingTrackID = nil
                self.preparingEntryID = nil
                self.playingEntryID = entry.id
                self.player.play(track: entry.track, fileURL: fileURL)
                self.startPrefetch()
            } catch {
                guard !Task.isCancelled,
                      self.playbackGate.accepts(token),
                      self.queue.currentID == entry.id else {
                    return
                }
                self.preparingTrackID = nil
                self.preparingEntryID = nil
                self.player.stop()
                self.playingEntryID = nil
                self.errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? "Could not play this track."
            }
        }
    }

    private func cancelPreparation() {
        cancelRadio()
        guard preparingEntryID != nil else { return }
        playbackGate.next()
        preparationTask?.cancel()
        preparationTask = nil
        prefetchTask?.cancel()
        prefetchTask = nil
        preparingTrackID = nil
        preparingEntryID = nil
    }

    private func advanceQueueAfterEnd() {
        guard isCurrentTrackLoaded, preparingEntryID == nil else { return }
        guard let entry = queue.advanceAfterEnd() else {
            return
        }
        beginPlayback(entry)
    }

    private func handlePlaybackFailure(_ message: String) {
        preparingTrackID = nil
        preparingEntryID = nil
        prefetchTask?.cancel()
        prefetchTask = nil
        errorMessage = message
    }

    private func restartPrefetch() {
        prefetchTask?.cancel()
        prefetchTask = nil
        if queue.current != nil {
            startPrefetch()
        }
    }

    private func startPrefetch() {
        guard let api = client, let currentIndex = queue.currentIndex else { return }
        let candidates = Array(queue.entries.dropFirst(currentIndex + 1).prefix(2))
        guard !candidates.isEmpty else { return }
        let token = playbackGate.value
        let currentID = queue.currentID
        let protected = Set(
            ([queue.current?.track.id] + candidates.map { Optional($0.track.id) }).compactMap { $0 }
        )
        prefetchTask = Task { [weak self] in
            for candidate in candidates {
                guard let self,
                      !Task.isCancelled,
                      self.playbackGate.accepts(token),
                      self.queue.currentID == currentID,
                      self.client != nil else {
                    return
                }
                _ = try? await api.downloadAudio(
                    for: candidate.track,
                    protectingTrackIDs: protected
                )
            }
        }
    }

    private func performSearch(_ query: String) async {
        guard let api = client else { return }
        isSearching = true
        defer {
            if currentQuery == query {
                isSearching = false
            }
        }
        do {
            let tracks = try await api.search(query)
            guard !Task.isCancelled, client != nil, currentQuery == query else { return }
            results = tracks
        } catch {
            guard !Task.isCancelled, client != nil, currentQuery == query else { return }
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Search failed. Try again."
        }
    }

    private var currentQuery: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func normalizedServerURL(_ raw: String) throws -> URL {
        var trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") {
            trimmed.removeLast()
        }
        guard let components = URLComponents(string: trimmed),
              components.scheme?.lowercased() == "https",
              let host = components.host,
              !host.isEmpty,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              let url = components.url else {
            throw APIError.invalidConfiguration(
                "Enter an HTTPS address such as https://ember.your-tailnet.ts.net/ember."
            )
        }
        return url
    }
}
