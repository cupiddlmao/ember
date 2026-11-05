import Foundation

struct SavedPlaylist: Codable, Identifiable, Equatable {
    let id: String
    let title: String
    let owner: String
    let artworkUrl: URL?
    let trackCount: Int
    let unavailableCount: Int
    let savedAt: Date

    init(summary: PlaylistSummary) {
        id = summary.id
        title = summary.title
        owner = summary.owner
        artworkUrl = summary.artworkUrl
        trackCount = summary.trackCount
        unavailableCount = summary.unavailableCount
        savedAt = Date()
    }

    var artwork: URL? { artworkUrl }
}

enum PlaylistStore {
    private static let filename = "SavedPlaylists.json"
    private static let maximumCount = 200

    static func load() -> [SavedPlaylist] {
        guard let url = try? fileURL(),
              let data = try? Data(contentsOf: url) else {
            return []
        }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let values = try? decoder.decode([SavedPlaylist].self, from: data) else {
            return []
        }
        var seen = Set<String>()
        return values.filter { playlist in
            guard !playlist.id.isEmpty, seen.insert(playlist.id).inserted else {
                return false
            }
            return true
        }.prefix(maximumCount).map { $0 }
    }

    static func save(_ playlists: [SavedPlaylist]) throws {
        var seen = Set<String>()
        let values = playlists.filter { playlist in
            guard !playlist.id.isEmpty, seen.insert(playlist.id).inserted else {
                return false
            }
            return true
        }.prefix(maximumCount)
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        let data = try encoder.encode(Array(values))
        let url = try fileURL()
        try data.write(
            to: url,
            options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication]
        )
    }

    private static func fileURL() throws -> URL {
        guard let support = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else {
            throw CocoaError(.fileNoSuchFile)
        }
        let directory = support.appendingPathComponent("Ember", isDirectory: true)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication]
        )
        return directory.appendingPathComponent(filename)
    }
}
