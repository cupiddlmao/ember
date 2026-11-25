import Foundation

@main
struct OfflineStoreCheck {
    @MainActor
    static func main() async throws {
        let manager = FileManager.default
        let root = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        let directory = root.appendingPathComponent("Offline")
        defer { try? manager.removeItem(at: root) }

        let store = OfflineStore(directory: directory)
        assert(store.playlists.isEmpty)
        assert(store.pinnedTrackIDs().isEmpty)

        // Track ID gate: only 11-character video IDs become file names.
        assert(OfflineStore.isTrackID("dQw4w9WgXcQ"))
        assert(!OfflineStore.isTrackID("short"))
        assert(!OfflineStore.isTrackID("../../etc/passwd"))
        assert(!OfflineStore.isTrackID("dQw4w9WgXc!"))

        func track(_ id: String) -> SharedPlaylistEntry {
            SharedPlaylistEntry(track: Track(
                id: id, title: "A", artist: "B", album: "C", durationMs: 1, artworkUrl: nil
            ))
        }
        let playlist = SharedPlaylist(
            id: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            name: "Test",
            revision: 3,
            createdAt: "2026-01-01T00:00:00Z",
            updatedAt: "2026-01-01T00:00:00Z",
            trackCount: 2,
            entries: [track("dQw4w9WgXcQ"), track("aaaaaaaaaaa")]
        )

        // A download against an unreachable server creates the manifest and
        // leaves it incomplete; nothing on disk counts as downloaded yet.
        let unreachable = APIClient(baseURL: URL(string: "https://127.0.0.1:9/ember")!, token: "k")
        await store.download(playlist: playlist, api: unreachable)
        var state = store.state(for: playlist.id)
        assert(state?.trackIDs == ["dQw4w9WgXcQ", "aaaaaaaaaaa"], "the manifest pins its tracks")
        assert(state?.complete == false, "no files yet, so the download is partial")
        assert(state?.revision == 3)
        assert(store.fileURL(for: "dQw4w9WgXcQ") == nil)

        // Files appearing on disk make the manifest complete after a sync.
        try Data(repeating: 7, count: 4096).write(to: directory.appendingPathComponent("dQw4w9WgXcQ.m4a"))
        try Data(repeating: 7, count: 8192).write(to: directory.appendingPathComponent("aaaaaaaaaaa.m4a"))
        try store.updateManifest(for: playlist)
        state = store.state(for: playlist.id)
        assert(state?.complete == true, "both files exist so the manifest is complete")
        assert(store.fileURL(for: "dQw4w9WgXcQ") != nil)
        assert(store.contains("aaaaaaaaaaa"))

        // A truncated file is not a download.
        try Data().write(to: directory.appendingPathComponent("aaaaaaaaaaa.m4a"))
        assert(store.contains("aaaaaaaaaaa") == false)
        try Data(repeating: 7, count: 8192).write(to: directory.appendingPathComponent("aaaaaaaaaaa.m4a"))

        // Persistence round-trip survives a new store over the same directory.
        let reloaded = OfflineStore(directory: directory)
        assert(reloaded.state(for: playlist.id)?.trackIDs == ["dQw4w9WgXcQ", "aaaaaaaaaaa"])
        assert(reloaded.contains("dQw4w9WgXcQ"))

        // A revision change updates the manifest and deletes unshared audio.
        let changed = SharedPlaylist(
            id: playlist.id, name: "Test", revision: 4,
            createdAt: playlist.createdAt, updatedAt: playlist.updatedAt, trackCount: 2,
            entries: [track("dQw4w9WgXcQ"), track("bbbbbbbbbbb")]
        )
        try store.updateManifest(for: changed)
        let updated = store.state(for: playlist.id)
        assert(updated?.trackIDs == ["dQw4w9WgXcQ", "bbbbbbbbbbb"])
        assert(updated?.revision == 4)
        assert(!manager.fileExists(atPath: directory.appendingPathComponent("aaaaaaaaaaa.m4a").path),
               "unshared audio from the old revision is deleted")
        assert(manager.fileExists(atPath: directory.appendingPathComponent("dQw4w9WgXcQ.m4a").path))

        // A load that carries no entries must never wipe a manifest.
        let empty = SharedPlaylist(
            id: playlist.id, name: "Test", revision: 5,
            createdAt: playlist.createdAt, updatedAt: playlist.updatedAt, trackCount: 0,
            entries: nil
        )
        try store.updateManifest(for: empty)
        assert(store.state(for: playlist.id)?.trackIDs == ["dQw4w9WgXcQ", "bbbbbbbbbbb"],
               "an entries-less load keeps the manifest")

        // Orphaned audio (crash leftovers, races with removal) is swept when no
        // manifest pins it. A fresh store sweeps at init.
        try Data(repeating: 7, count: 2048).write(
            to: directory.appendingPathComponent("eeeeeeeeeee.m4a")
        )
        let sweeper = OfflineStore(directory: directory)
        assert(!manager.fileExists(atPath: directory.appendingPathComponent("eeeeeeeeeee.m4a").path),
               "a new store sweeps audio nothing pins")
        _ = sweeper

        // Audio another playlist still pins survives removing one playlist.
        // Both manifests live on one store so the pins are actually shared.
        let second = SharedPlaylist(
            id: "ffffffff-0000-4ccc-8ddd-111111111111",
            name: "Second", revision: 1,
            createdAt: playlist.createdAt, updatedAt: playlist.updatedAt, trackCount: 1,
            entries: [track("dQw4w9WgXcQ")]
        )
        await store.download(playlist: second, api: unreachable)
        assert(store.state(for: second.id)?.trackIDs == ["dQw4w9WgXcQ"])
        try store.remove(playlistID: playlist.id)
        assert(store.state(for: playlist.id) == nil)
        assert(manager.fileExists(atPath: directory.appendingPathComponent("dQw4w9WgXcQ.m4a").path),
               "shared audio survives removal")
        assert(!manager.fileExists(atPath: directory.appendingPathComponent("bbbbbbbbbbb.m4a").path),
               "audio nothing else pins goes with the playlist")

        try store.remove(playlistID: second.id)
        assert(!manager.fileExists(atPath: directory.appendingPathComponent("dQw4w9WgXcQ.m4a").path))
        assert(store.bytesUsed() == 0)
        assert(OfflineStore.byteText(4096).contains("4"))
        print("Offline store checks passed")
    }
}
