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
