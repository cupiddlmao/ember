import Foundation

@main
struct AudioCacheCheck {
    @MainActor
    static func main() throws {
        let manager = FileManager.default
        let directory = manager.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try manager.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? manager.removeItem(at: directory) }
        func file(_ name: String, age: TimeInterval) throws -> URL {
            let url = directory.appendingPathComponent(name)
            try Data(repeating: 1, count: 4).write(to: url)
            try manager.setAttributes([.modificationDate: Date(timeIntervalSince1970: age)],
                                      ofItemAtPath: url.path)
            return url
        }
        let playing = try file("playing.m4a", age: 0)
        let oldest = try file("oldest.m4a", age: 1)
        let recent = try file("recent.m4a", age: 2)
        let incoming = try file("incoming.m4a", age: 3)
        let unrelated = try file("keep.txt", age: 0)
        try APIClient.trimAudioCache(directory, protecting: ["playing.m4a", "incoming.m4a"], maxBytes: 12)
        assert(!manager.fileExists(atPath: oldest.path))
        assert(manager.fileExists(atPath: recent.path))
        assert(manager.fileExists(atPath: playing.path))
        assert(manager.fileExists(atPath: incoming.path))
        assert(manager.fileExists(atPath: unrelated.path))
        try APIClient.trimAudioCache(directory, protecting: ["playing.m4a", "incoming.m4a"], maxBytes: 8)
        assert(!manager.fileExists(atPath: recent.path))
        try APIClient.trimAudioCache(directory, protecting: ["playing.m4a", "incoming.m4a"], maxBytes: 8)
        print("Audio cache checks passed")
    }
}
