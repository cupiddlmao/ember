import Foundation

private final class LastFMScript: @unchecked Sendable {
    enum Reply { case json(Int, Data), failure(URLError) }
    private let lock = NSLock()
    private var replies: [Reply] = []
    private var recorded: [URLRequest] = []

    func enqueue(status: Int, _ object: Any) {
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
        recorded.append(request)
        precondition(!replies.isEmpty, "Unexpected Last.fm request")
        return replies.removeFirst()
    }

    func requests() -> [URLRequest] {
        lock.lock(); defer { lock.unlock() }
        return recorded
    }

    func bodies() -> [[String: Any]] {
        requests().compactMap { request in
            guard let data = request.httpBody,
                  let body = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            return body
        }
    }

    func count(_ action: String) -> Int {
        bodies().filter { $0["action"] as? String == action }.count
    }
}

private final class LastFMProtocol: URLProtocol {
    static let script = LastFMScript()

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
        case .failure(let error):
            client?.urlProtocol(self, didFailWithError: error)
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
struct LastFMCheck {
    @MainActor
    static func main() async throws {
        var clock = ListeningClock()
        clock.sample(position: 0, time: 0, playing: true)
        clock.sample(position: 1, time: 1, playing: true)
        clock.sample(position: 1, time: 2, playing: false)
        clock.sample(position: 1, time: 100, playing: false)
        clock.sample(position: 1, time: 101, playing: true)
        clock.sample(position: 2, time: 102, playing: true)
        clock.sample(position: 100, time: 103, playing: true)
        clock.sample(position: 100, time: 104, playing: true)
        assert(clock.listened == 2)
        clock.breakContinuity()
        clock.sample(position: 101, time: 105, playing: true)
        assert(clock.listened == 2)
        clock.sample(position: 102, time: 106, playing: true)
        assert(clock.listened == 3)
        print("Last.fm pause, buffering, seek and suspension timing checks passed")

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let outbox = directory.appendingPathComponent("outbox.json")
        let base = URL(string: "https://example.invalid/ember")!
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [LastFMProtocol.self]
        let session = URLSession(configuration: configuration)
        defer { session.invalidateAndCancel() }
        let api = APIClient(baseURL: base, token: "test-only", session: session)
        let script = LastFMProtocol.script

        @MainActor func writeOutbox(server: String, pending: [[String: Any]]) throws {
            let data = try JSONSerialization.data(withJSONObject: [
                "server": server, "account": "", "pending": pending
            ])
            try data.write(to: outbox)
        }

        func outboxValue() -> [String: Any] {
            guard let data = try? Data(contentsOf: outbox),
                  let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
            return value
        }

        func pendingCount() -> Int {
            (outboxValue()["pending"] as? [[String: Any]])?.count ?? -1
        }

        func event(_ id: String) -> [String: Any] {
            ["action": "scrobble", "id": id, "title": "Song", "artist": "Artist",
             "album": "", "duration": 180, "listened": 95, "started": 0]
        }

        @MainActor func makeReporter() -> LastFMReporter {
            let reporter = LastFMReporter(outboxURL: outbox)
            reporter.client = { api }
            return reporter
        }

        func queueStatus() {
            script.enqueue(status: 200, ["connected": true, "username": "tester",
                                         "configured": true, "pending": 0])
        }

        func drain(_ yields: Int = 200) async {
            for _ in 0..<yields { await Task.yield() }
        }

        @MainActor func waitFor(_ label: String, _ condition: () -> Bool) async {
            for _ in 0..<10_000 {
                if condition() { return }
                await Task.yield()
            }
            preconditionFailure("Last.fm check timed out: \(label)")
        }

        @MainActor func prime(_ reporter: LastFMReporter, statuses: Int) async {
            reporter.sample(track: nil, position: 0, duration: 0, playing: false)
            await waitFor("status \(statuses)") { script.count("status") == statuses }
            await drain()
        }

        @MainActor func attemptSend(_ reporter: LastFMReporter, scrobbles: Int) async {
            for _ in 0..<40 {
                reporter.sample(track: nil, position: 0, duration: 0, playing: false)
                await drain(20)
                if script.count("scrobble") >= scrobbles { break }
            }
            await drain()
        }

        // A send that fails must leave the event for the next launch to retry.
        try writeOutbox(server: base.absoluteString, pending: [event("one")])
        queueStatus()
        script.fail(.timedOut)
        let first = makeReporter()
        await prime(first, statuses: 1)
        await attemptSend(first, scrobbles: 1)
        assert(pendingCount() == 1, "a failed send must retain the outbox event")

        // A fresh reporter reads the persisted event and clears it after success.
        queueStatus()
        script.enqueue(status: 200, [:])
        let second = makeReporter()
        await prime(second, statuses: 2)
        await attemptSend(second, scrobbles: 2)
        assert(pendingCount() == 0, "a relaunched reporter must retry and clear the outbox")
        let retried = script.bodies().filter { $0["action"] as? String == "scrobble" }
        assert(retried.count == 2)
        assert(retried.allSatisfy { $0["id"] as? String == "one" })

        // A 400 is permanent: drop the event instead of blocking the outbox.
        try writeOutbox(server: base.absoluteString, pending: [event("two")])
        queueStatus()
        script.enqueue(status: 400, ["error": ["message": "rejected"]])
        let third = makeReporter()
        await prime(third, statuses: 3)
        await attemptSend(third, scrobbles: 3)
        assert(pendingCount() == 0, "a 400 must drop the rejected event")

        // Another server's outbox must never be replayed into this account.
        try writeOutbox(server: "https://old.invalid/ember", pending: [event("three")])
        queueStatus()
        let scrobblesBefore = script.count("scrobble")
        let switched = makeReporter()
        await prime(switched, statuses: 4)
        await drain()
        assert(outboxValue()["server"] as? String == base.absoluteString,
               "the outbox must follow the current server")
        assert(pendingCount() == 0, "another server's outbox must be cleared")
        assert(script.count("scrobble") == scrobblesBefore,
               "another server's event must not be sent")

        print("Last.fm outbox retry, 400 drop and server-switch checks passed")
    }
}
