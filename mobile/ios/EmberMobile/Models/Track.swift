import Foundation

struct Track: Identifiable, Decodable, Equatable {
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
