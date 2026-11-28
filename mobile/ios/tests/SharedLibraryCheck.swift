import Foundation

private final class LibraryScript: @unchecked Sendable {
    enum Reply { case json(Int, Data), failure(URLError) }
    private let lock = NSLock()
    private var replies: [Reply] = []
    private var requests: [URLRequest] = []

    func enqueue(_ status: Int, _ object: Any) {
        let data = try! JSONSerialization.data(withJSONObject: object)
        lock.lock(); defer { lock.unlock() }
        replies.append(.json(status, data))
    }
    func fail(_ code: URLError.Code) {
        lock.lock(); defer { lock.unlock() }
        replies.append(.failure(URLError(code)))
    }
    func next(_ request: URLRequest) -> Reply {
        lock.lock(); defer { lock.unlock() }
        requests.append(request)
        precondition(!replies.isEmpty, "Unexpected library network request")
        return replies.removeFirst()
    }
    func writes() -> [URLRequest] {
        lock.lock(); defer { lock.unlock() }
        return requests.filter { $0.httpMethod == "POST" || $0.httpMethod == "PUT" }
    }
    func deletions() -> [URLRequest] {
        lock.lock(); defer { lock.unlock() }
        return requests.filter { $0.httpMethod == "DELETE" }
    }
}

private final class LibraryProtocol: URLProtocol {
    static let script = LibraryScript()
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        var captured = request
        if captured.httpBody == nil, let stream = captured.httpBodyStream {
            stream.open(); defer { stream.close() }
            var data = Data()
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                guard count > 0 else { break }
                data.append(contentsOf: buffer.prefix(count))
            }
            captured.httpBody = data
        }
        switch Self.script.next(captured) {
        case .failure(let error): client?.urlProtocol(self, didFailWithError: error)
        case .json(let status, let data):
            let response = HTTPURLResponse(url: request.url!, statusCode: status,
                httpVersion: "HTTP/1.1", headerFields: ["Content-Type": "application/json"])!
            client?.urlProtocol(self, didReceive: response, cacheStoragePolicy: .notAllowed)
            client?.urlProtocol(self, didLoad: data)
            client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() {}
}

@main
struct SharedLibraryCheck {
    @MainActor
    static func main() async throws {
        let thumbnail = URL(string: "https://i.ytimg.com/vi/L_r0ZCORnJk/hqdefault.jpg")!
        assert(ArtworkURL.display(thumbnail)?.lastPathComponent == "maxresdefault.jpg")
        assert(ArtworkURL.display(thumbnail, fallback: true)?.lastPathComponent == "mqdefault.jpg")
        let album = URL(string: "https://yt3.googleusercontent.com/cover=w544-h544")!
        assert(ArtworkURL.display(album) == album)
        let otherHost = URL(string: "https://example.invalid/vi/L_r0ZCORnJk/hqdefault.jpg")!
        assert(ArtworkURL.display(otherHost) == otherHost)
        let badID = URL(string: "https://i.ytimg.com/vi/short/hqdefault.jpg")!
        assert(ArtworkURL.display(badID) == badID)
        assert(ArtworkURL.display(nil) == nil)

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LibraryProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: URL(string: "https://example.invalid/ember")!, token: "test-only", session: session)
        let model = SharedLibraryModel(cacheDirectory: directory)
        model.configure(api)
        let script = LibraryProtocol.script
        script.enqueue(200, ["playlists": []])
        await model.refresh()
        assert(model.canEdit)

        let track = Track(id: "dQw4w9WgXcQ", title: "Song", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let accented = Track(id: "dQw4w9WgXcQ", title: "Élan", artist: "Saosin", album: "Translating the Name", durationMs: 1000, artworkUrl: nil)
        assert(accented.matchesPlaylistSearch(" ELAN "))
        assert(accented.matchesPlaylistSearch("SAOSIN"))
        assert(accented.matchesPlaylistSearch("translating"))
        assert(accented.matchesPlaylistSearch("  "))
        assert(!accented.matchesPlaylistSearch("missing"))
        let searchEntries = [SharedPlaylistEntry(track: track), SharedPlaylistEntry(track: accented), SharedPlaylistEntry(track: accented)]
        let matches = searchEntries.enumerated().filter { $0.element.track.matchesPlaylistSearch("elan") }
        assert(matches.map(\.offset) == [1, 2])
        assert(matches[0].element.id != matches[1].element.id)
        var afterFilteredDelete = searchEntries
        let originalIndices = matches.map(\.offset)
        afterFilteredDelete.remove(at: originalIndices[1])
        assert(afterFilteredDelete.map(\.id) == [searchEntries[0].id, searchEntries[1].id])
        let entries = [SharedPlaylistEntry(track: track), SharedPlaylistEntry(track: track)]
        assert(entries[0].id != entries[1].id)
        let command = LibraryMutation.create(name: "GG", entries: entries)
        script.fail(.timedOut)
        let firstSave = await model.submit(command)
        assert(!firstSave && model.pending?.body.requestId == command.body.requestId && model.canEdit)
        assert(model.queue.count == 1)

        let restored = SharedLibraryModel(cacheDirectory: directory)
        restored.configure(api)
        assert(restored.pending?.body.requestId == command.body.requestId && restored.canEdit)
        assert(restored.queue.count == 1)
        let encodedEntries = try JSONSerialization.jsonObject(with: JSONEncoder().encode(entries))
        let summary: [String: Any] = ["id": command.playlistID, "name": "GG", "revision": 1,
            "createdAt": "2026-11-06T00:00:00+00:00", "updatedAt": "2026-11-06T00:00:00+00:00", "trackCount": 2]
        script.enqueue(201, ["id": command.playlistID, "revision": 1, "deleted": false])
        script.enqueue(200, ["playlists": [summary]])
        script.enqueue(200, summary.merging(["entries": encodedEntries]) { _, new in new })
        let retrySaved = await restored.retryPending()
        assert(retrySaved && restored.pending == nil && restored.canEdit)
        let snapshot = restored.details[command.playlistID]!
        assert(snapshot.entries?.map(\.id) == entries.map(\.id))
        let writes = script.writes()
        assert(writes.count == 2 && writes[0].httpBody == writes[1].httpBody)
        assert(writes[0].url?.path == "/ember/v1/library/playlists")
        assert(writes[0].value(forHTTPHeaderField: "Authorization") == "Bearer test-only")
        let body = try JSONSerialization.jsonObject(with: writes[0].httpBody!) as! [String: Any]
        assert(body["expectedRevision"] == nil && body["requestId"] as? String == command.body.requestId)

        let renamedSummary = summary.merging(["name": "Renamed", "revision": 2]) { _, new in new }
        let rename = LibraryMutation.update(snapshot, name: "Renamed", entries: entries)
        script.enqueue(200, ["id": command.playlistID, "revision": 2, "deleted": false])
        script.enqueue(200, ["playlists": [renamedSummary]])
        script.enqueue(200, renamedSummary.merging(["entries": encodedEntries]) { _, new in new })
        let renamed = await restored.submit(rename)
        assert(renamed && restored.details[command.playlistID]?.name == "Renamed")
        assert(restored.details[command.playlistID]?.entries?.map(\.id) == entries.map(\.id))

        let update = LibraryMutation.update(snapshot, name: "Renamed", entries: [])
        script.enqueue(409, ["error": ["code": "revision_conflict", "message": "Changed elsewhere"]])
        script.enqueue(200, renamedSummary.merging(["entries": encodedEntries]) { _, new in new })
        script.enqueue(200, ["playlists": [renamedSummary]])
        script.enqueue(200, renamedSummary.merging(["entries": encodedEntries]) { _, new in new })
        let conflictSaved = await restored.submit(update)
        assert(!conflictSaved && restored.queue.isEmpty && restored.canEdit)
        assert(restored.details[command.playlistID]?.entries?.count == 2)
        assert(restored.errorMessage?.contains("another device") == true)

        let offline = SharedLibraryModel(cacheDirectory: directory)
        offline.configure(api)
        script.fail(.notConnectedToInternet)
        await offline.refresh()
        assert(offline.canEdit && offline.playlists.count == 1 && offline.queue.isEmpty)
        assert(offline.details[command.playlistID]?.entries?.count == 2)
        let other = APIClient(baseURL: URL(string: "https://other.invalid/ember")!, token: "test-only", session: session)
        offline.configure(other)
        assert(offline.playlists.isEmpty && offline.details.isEmpty && offline.pending == nil)

        // Offline edits queue optimistically, survive a relaunch, and replay
        // in order with the revisions the optimistic chain predicted.
        offline.configure(api)
        assert(offline.queue.isEmpty && offline.details[command.playlistID]?.entries?.count == 2)
        let extra = Track(id: "aaa111bbb22", title: "Extra", artist: "Artist", album: "", durationMs: 1000, artworkUrl: nil)
        let cached = offline.details[command.playlistID]!
        let grown = (cached.entries ?? []) + [SharedPlaylistEntry(track: extra)]
        let firstQueued = LibraryMutation.update(cached, name: cached.name, entries: grown)
        let firstSubmitted = await offline.submit(firstQueued)
        assert(firstSubmitted && offline.queue.count == 1 && !offline.isOnline)
        assert(offline.details[command.playlistID]?.entries?.count == 3)
        let secondQueued = LibraryMutation.update(offline.details[command.playlistID]!, name: "Offline name", entries: grown)
        let secondSubmitted = await offline.submit(secondQueued)
        assert(secondSubmitted && offline.queue.count == 2)
        assert(offline.details[command.playlistID]?.name == "Offline name")
        let relaunched = SharedLibraryModel(cacheDirectory: directory)
        relaunched.configure(api)
        assert(relaunched.queue.map { $0.body.requestId } == [firstQueued.body.requestId, secondQueued.body.requestId])
        let grownJSON = try JSONSerialization.jsonObject(with: JSONEncoder().encode(grown))
        let finalSummary = summary.merging(["name": "Offline name", "revision": 4, "trackCount": 3]) { _, new in new }
        script.enqueue(200, ["id": command.playlistID, "revision": 3, "deleted": false])
        script.enqueue(200, ["id": command.playlistID, "revision": 4, "deleted": false])
        script.enqueue(200, ["playlists": [finalSummary]])
        script.enqueue(200, finalSummary.merging(["entries": grownJSON]) { _, new in new })
        let synced = await relaunched.retryPending()
        assert(synced && relaunched.queue.isEmpty)
        assert(relaunched.details[command.playlistID]?.name == "Offline name")
        assert(relaunched.details[command.playlistID]?.revision == 4)
        assert(relaunched.details[command.playlistID]?.entries?.count == 3)
        let queuedWrites = Array(script.writes().suffix(2))
        let firstBody = try JSONSerialization.jsonObject(with: queuedWrites[0].httpBody!) as! [String: Any]
        let secondBody = try JSONSerialization.jsonObject(with: queuedWrites[1].httpBody!) as! [String: Any]
        assert(firstBody["expectedRevision"] as? Int == 2 && secondBody["expectedRevision"] as? Int == 3)

        // A delete that hits a conflict re-issues against the fresh revision
        // instead of silently dropping or overwriting the other device.
        let target = relaunched.details[command.playlistID]!
        script.enqueue(409, ["error": ["code": "revision_conflict", "message": "Changed elsewhere"]])
        script.enqueue(200, finalSummary.merging(["name": "Elsewhere", "revision": 5]) { _, new in new }
            .merging(["entries": grownJSON]) { _, new in new })
        script.enqueue(200, ["id": command.playlistID, "revision": 6, "deleted": true])
        script.enqueue(200, ["playlists": []])
        let offlineDeleted = await relaunched.submit(.delete(target))
        assert(offlineDeleted && relaunched.queue.isEmpty && relaunched.playlists.isEmpty)
        assert(relaunched.details[command.playlistID] == nil)
        let deleteWrites = script.deletions()
        let refusedDelete = try JSONSerialization.jsonObject(with: deleteWrites[0].httpBody!) as! [String: Any]
        let reissuedDelete = try JSONSerialization.jsonObject(with: deleteWrites[1].httpBody!) as! [String: Any]
        assert(refusedDelete["expectedRevision"] as? Int == 4 && reissuedDelete["expectedRevision"] as? Int == 5)

        script.enqueue(200, ["playlists": [renamedSummary]])
        await restored.refresh()
        let deletion = LibraryMutation.delete(restored.details[command.playlistID]!)
        script.enqueue(200, ["id": command.playlistID, "revision": 3, "deleted": true])
        script.enqueue(200, ["playlists": []])
        let deleted = await restored.submit(deletion)
        assert(deleted && restored.playlists.isEmpty && restored.details[command.playlistID] == nil)

        print("Shared library: duplicate entries, authenticated paths, persisted identical retries, rename/delete, conflict safety, offline edit queueing with ordered replay and delete-conflict reissue passed")
    }
}
