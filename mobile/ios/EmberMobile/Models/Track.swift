import Foundation

struct Track: Identifiable, Codable, Equatable {
    let id: String
    let title: String
    let artist: String
    let album: String
    let durationMs: Int
    let artworkUrl: URL?

    var duration: Double {
        max(0, Double(durationMs) / 1000)
    }

    var durationText: String {
        Track.format(duration)
    }

    func matchesPlaylistSearch(_ query: String) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        return needle.isEmpty || [title, artist, album].contains {
            $0.range(of: needle, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }

    static func format(_ seconds: Double) -> String {
        guard seconds.isFinite, seconds >= 0 else { return "0:00" }
        let total = Int(seconds.rounded(.down))
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

struct SearchResponse: Decodable {
    let tracks: [Track]
}

struct APIErrorBody: Decodable {
    struct Detail: Decodable {
        let code: String
        let message: String
    }

    let error: Detail
}

struct PlaylistSummary: Identifiable, Decodable, Equatable {
    let id: String
    let title: String
    let owner: String
    let artworkUrl: URL?
    let trackCount: Int
    let unavailableCount: Int

    var artwork: URL? { artworkUrl }
}

struct PlaylistPage: Decodable {
    let playlist: PlaylistSummary
    let tracks: [Track]
    let offset: Int
    let limit: Int
    let total: Int
    let hasMore: Bool
}

struct LyricsWord: Decodable, Equatable {
    let text: String
    let startMs: Int?
    let endMs: Int?

    var start: Double? { startMs.map { Double($0) / 1000 } }
    var end: Double? { endMs.map { Double($0) / 1000 } }
}

struct LyricsLine: Decodable, Equatable {
    let text: String
    let startMs: Int?
    let endMs: Int?
    let words: [LyricsWord]

    var start: Double? { startMs.map { Double($0) / 1000 } }
    var end: Double? { endMs.map { Double($0) / 1000 } }
    var isTimed: Bool { startMs != nil }
}

struct LyricsResponse: Decodable {
    let trackId: String
    let source: String
    let kind: String
    let lines: [LyricsLine]
    let message: String?
}

// Ember-owned playlists are separate from saved provider bookmarks.
struct SharedPlaylistEntry: Codable, Identifiable, Equatable {
    let id: String
    let track: Track

    init(track: Track) {
        id = UUID().uuidString.lowercased()
        self.track = track
    }
}

struct SharedPlaylist: Codable, Identifiable, Equatable {
    let id: String
    let name: String
    let revision: Int
    let createdAt: String
    let updatedAt: String
    let trackCount: Int
    let entries: [SharedPlaylistEntry]?
}

struct SharedPlaylistList: Decodable {
    let playlists: [SharedPlaylist]
}

struct LibraryReceipt: Decodable {
    let id: String
    let revision: Int
    let deleted: Bool
}

struct LibraryMutation: Codable {
    enum Operation: String, Codable { case create, update, delete }
    let operation: Operation
    let playlistID: String
    let body: Body

    struct Body: Codable {
        let requestId: String
        let id: String?
        let expectedRevision: Int?
        let name: String?
        let entries: [SharedPlaylistEntry]?
    }

    static func create(name: String, entries: [SharedPlaylistEntry] = []) -> Self {
        let id = UUID().uuidString.lowercased()
        return Self(operation: .create, playlistID: id,
                    body: Body(requestId: UUID().uuidString.lowercased(), id: id,
                               expectedRevision: nil, name: name, entries: entries))
    }

    static func update(_ playlist: SharedPlaylist, name: String, entries: [SharedPlaylistEntry]) -> Self {
        Self(operation: .update, playlistID: playlist.id,
             body: Body(requestId: UUID().uuidString.lowercased(), id: nil,
                        expectedRevision: playlist.revision, name: name, entries: entries))
    }

    static func delete(_ playlist: SharedPlaylist) -> Self {
        Self(operation: .delete, playlistID: playlist.id,
             body: Body(requestId: UUID().uuidString.lowercased(), id: nil,
                        expectedRevision: playlist.revision, name: nil, entries: nil))
    }
}

// YouTube's 4:3 thumbnails include letterboxing; use widescreen frames before square cropping.
enum ArtworkURL {
    static func display(_ original: URL?, fallback: Bool = false) -> URL? {
        guard let original,
              original.scheme == "https",
              let host = original.host,
              ["i.ytimg.com", "img.youtube.com"].contains(host),
              original.user == nil, original.password == nil, original.port == nil,
              var parts = URLComponents(url: original, resolvingAgainstBaseURL: false)
        else { return original }
        let path = parts.path.split(separator: "/")
        guard path.count == 3, path[0] == "vi",
              path[1].count == 11,
              path[1].allSatisfy({ $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "_" || $0 == "-") }),
              ["default.jpg", "mqdefault.jpg", "hqdefault.jpg", "sddefault.jpg", "maxresdefault.jpg"].contains(String(path[2]))
        else { return original }
        parts.path = "/vi/\(path[1])/\(fallback ? "mqdefault.jpg" : "maxresdefault.jpg")"
        return parts.url ?? original
    }
}
