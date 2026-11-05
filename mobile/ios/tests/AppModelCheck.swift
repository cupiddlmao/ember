import Foundation

// Compile the real AppModel against deterministic network/audio doubles on macOS.
@MainActor
struct APIClient {
    static var pending: [String: CheckedContinuation<URL, Error>] = [:]
    init(baseURL: URL, token: String) {}
    func health() async throws {}
    func search(_ query: String) async throws -> [Track] { [] }
    func previewPlaylist(url: String) async throws -> PlaylistPage { throw URLError(.badURL) }
    func playlistPage(playlistID: String, offset: Int, limit: Int) async throws -> PlaylistPage {
        throw URLError(.badURL)
    }
    func lyrics(for track: Track) async throws -> LyricsResponse { throw URLError(.badURL) }
    func downloadAudio(for track: Track, protectingTrackIDs: Set<String>) async throws -> URL {
        try await withCheckedThrowingContinuation { Self.pending[track.id] = $0 }
    }
}

enum APIError: LocalizedError {
    case invalidConfiguration(String)
    var errorDescription: String? {
        switch self { case .invalidConfiguration(let message): return message }
    }
}

struct SavedPlaylist {
    let id: String
    init(summary: PlaylistSummary) { id = summary.id }
}

enum PlaylistStore {
    static func load() -> [SavedPlaylist] { [] }
    static func save(_ playlists: [SavedPlaylist]) throws {}
}

enum KeychainStore {
    static func read() -> String? { nil }
    static func save(_ key: String) throws {}
    static func delete() {}
}

@MainActor
final class PlayerController {
    enum PlaybackState { case idle, loading, playing, paused, failed }
    var state: PlaybackState = .idle
    var track: Track?
    var currentTime: Double = 0
    var played: [String] = []
    var onTrackEnded: (() -> Void)?
    var onPlaybackFailed: ((String) -> Void)?
    var onNext: (() -> Void)?
    var onPrevious: (() -> Void)?
    var onPauseRequested: (() -> Void)?
    var onResumeRequested: (() -> Bool)?
    var isPreparingPlayback: (() -> Bool)?
    func pause() { onPauseRequested?(); state = .paused }
    func stop() { track = nil; state = .idle }
    func seek(to seconds: Double) { currentTime = seconds }
    func play(track: Track, fileURL: URL) {
        self.track = track
        state = .playing
        played.append(track.id)
    }
}

@main
struct AppModelCheck {
    @MainActor
    static func waitUntil(_ condition: () -> Bool) async {
        for _ in 0..<10_000 {
            if condition() { return }
            await Task.yield()
        }
        preconditionFailure("AppModel did not reach the expected state")
    }

    @MainActor
    static func main() async {
        let model = AppModel()
        model.serverURL = "https://example.invalid/ember"
        model.accessKey = "test-only"
        await model.connect()
        assert(model.isConnected)
        let a = Track(id: "aaaaaaaaaaa", title: "A", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let b = Track(id: "bbbbbbbbbbb", title: "B", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let file = URL(fileURLWithPath: "/tmp/test.m4a")

        // A cancellation-ignoring download must never start after Pause.
        model.play(a)
        await waitUntil { APIClient.pending[a.id] != nil }
        model.player.pause()
        assert(model.preparingEntryID == nil && !model.isPlaybackActive)
        APIClient.pending.removeValue(forKey: a.id)!.resume(returning: file)
        for _ in 0..<100 { await Task.yield() }
        assert(model.player.played.isEmpty)
        assert(model.currentQueueEntry?.track == a)

        // Resume retries the selection, including when no AVPlayer item exists.
        assert(model.player.onResumeRequested?() == true)
        await waitUntil { APIClient.pending[a.id] != nil }
        APIClient.pending.removeValue(forKey: a.id)!.resume(returning: file)
        await waitUntil { model.player.played == [a.id] }
        assert(model.isCurrentTrackLoaded)

        // A failed first item keeps the queue available; Next recovers.
        model.play(tracks: [a, b], startingAt: 0)
        await waitUntil { APIClient.pending[a.id] != nil }
        model.player.onTrackEnded?()
        assert(model.currentQueueEntry?.track == a)
        APIClient.pending.removeValue(forKey: a.id)!.resume(throwing: URLError(.cannotLoadFromNetwork))
        await waitUntil { model.errorMessage != nil }
        assert(model.player.track == nil)
        assert(model.currentQueueEntry?.track == a && model.queue.upcoming.count == 1)
        model.playNextTrack()
        await waitUntil { APIClient.pending[b.id] != nil }
        APIClient.pending.removeValue(forKey: b.id)!.resume(returning: file)
        await waitUntil { model.player.played.last == b.id }
        assert(model.currentQueueEntry?.track == b)
        model.disconnect()
        print("AppModel cancellation, retry and queue recovery checks passed")
    }
}
