import Foundation

// Position deltas prevent seeks, pauses and stalled playback from earning listen time.
struct ListeningClock {
    private(set) var listened: Double = 0
    private var previous: (position: Double, time: Double, playing: Bool)?

    mutating func sample(position: Double, time: Double, playing: Bool) {
        defer { previous = (position, time, playing) }
        guard position.isFinite, time.isFinite, playing,
              let previous, previous.playing else { return }
        let wall = time - previous.time
        let advance = position - previous.position
        if wall > 0, wall <= 3, advance > 0, advance <= wall + 0.75 {
            listened += min(wall, advance)
        }
    }

    mutating func breakContinuity() { previous = nil }
}

@MainActor
final class LastFMReporter {
    var client: (() -> APIClient?)?
    private var clock = ListeningClock()
    private var playbackID = UUID().uuidString
    private var started = 0
    private var submitted = false
    private var lastNow = -Double.infinity
    private var wasPlaying = false
    private var busy = false
    private var lastAttempt = -Double.infinity
    private var server = ""
    private var pending: [[String: Any]] = []
    private var connected = false
    private var account = ""
    private var lastStatus = -Double.infinity

    private var file: URL? {
        guard let root = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else { return nil }
        return root.appendingPathComponent("Ember/lastfm-outbox.json")
    }

    init() {
        if let file, let attributes = try? FileManager.default.attributesOfItem(atPath: file.path),
           let size = attributes[.size] as? NSNumber, size.intValue <= 1048576,
           let data = try? Data(contentsOf: file),
           let value = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            account = value["account"] as? String ?? ""
            server = value["server"] as? String ?? ""
            pending = Array((value["pending"] as? [[String: Any]] ?? []).suffix(1000))
        }
    }

    func begin() {
        clock = ListeningClock()
        playbackID = UUID().uuidString
        started = 0
        submitted = false
        lastNow = -.infinity
        wasPlaying = false
    }

    func breakContinuity() { clock.breakContinuity(); wasPlaying = false }

    func sample(track: Track?, position: Double, duration: Double, playing: Bool) {
        let now = ProcessInfo.processInfo.systemUptime
        guard let api = client?() else { return }
        let currentServer = api.baseURL.absoluteString
        if server != currentServer {
            // Never replay another server's listening history into this account.
            server = currentServer
            pending.removeAll()
            connected = false
            lastStatus = -.infinity
            clock = ListeningClock()
            save()
        }
        if !busy, now - lastStatus >= 60 {
            busy = true
            lastStatus = now
            let targetServer = server
            Task { [weak self] in
                guard let self else { return }
                defer { self.busy = false }
                if let result = try? await api.lastFM(["action": "status"]), self.server == targetServer {
                    self.connected = result["connected"] as? Bool == true
                    let account = result["username"] as? String ?? ""
                    if !self.connected || (!self.account.isEmpty && self.account != account) {
                        self.pending.removeAll()
                        self.clock = ListeningClock()
                        self.save()
                    }
                    self.account = account
                    self.save()
                }
            }
            return
        }
        guard connected else { clock.breakContinuity(); return }
        clock.sample(position: position, time: now, playing: playing)
        var nowBody: [String: Any]?
        if let track, playing {
            if started == 0 { started = Int(Date().timeIntervalSince1970) }
            if !wasPlaying || now - lastNow >= 60 {
                nowBody = ["action": "now", "title": track.title, "artist": track.artist, "album": track.album]
                lastNow = now
            }
            if !submitted, duration > 30, clock.listened >= min(duration / 2, 240) {
                pending.append(["action": "scrobble", "id": playbackID, "title": track.title,
                                "artist": track.artist, "album": track.album, "duration": Int(duration),
                                "listened": Int(ceil(clock.listened)), "started": started])
                pending = Array(pending.suffix(1000))
                submitted = true
                save()
            }
        }
        wasPlaying = playing
        guard !busy, nowBody != nil || (!pending.isEmpty && now - lastAttempt >= 30) else { return }
        let event = pending.first
        let targetServer = server
        busy = true
        lastAttempt = now
        Task { [weak self] in
            guard let self else { return }
            defer { self.busy = false }
            if let event {
                do {
                    _ = try await api.lastFM(event)
                    self.completeFirst(event, server: targetServer)
                } catch APIError.server(let status, _) where status == 400 {
                    // Expired or permanently invalid events must not block later listens.
                    self.completeFirst(event, server: targetServer)
                } catch { }
            }
            // Do not send a now-playing update delayed behind an offline scrobble retry.
            if event == nil, let nowBody { _ = try? await api.lastFM(nowBody) }
        }
    }

    private func completeFirst(_ event: [String: Any], server targetServer: String) {
        guard server == targetServer,
              pending.first?["id"] as? String == event["id"] as? String else { return }
        pending.removeFirst()
        save()
    }

    private func save() {
        guard let file else { return }
        do {
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            let data = try JSONSerialization.data(withJSONObject: ["server": server, "account": account, "pending": pending])
            try data.write(to: file, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        } catch { /* A storage failure must not interrupt audio playback. */ }
    }
}
