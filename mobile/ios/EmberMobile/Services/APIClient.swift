import Foundation

enum APIError: LocalizedError {
    case invalidConfiguration(String)
    case unauthorized
    case server(Int, String?)
    case transport(String)
    case invalidResponse
    case fileTooLarge

    var errorDescription: String? {
        switch self {
        case .invalidConfiguration(let message):
            return message
        case .unauthorized:
            return "The server rejected the access key."
        case .server(let status, let message):
            if let message, !message.isEmpty {
                return message
            }
            return "The server returned HTTP \(status)."
        case .transport(let message):
            return message
        case .invalidResponse:
            return "The server returned an unexpected response."
        case .fileTooLarge:
            return "The downloaded audio is larger than expected."
        }
    }
}

@MainActor
struct APIClient {
    let identity = UUID()
    let baseURL: URL
    private let token: String
    private let session: URLSession

    init(baseURL: URL, token: String, session: URLSession? = nil) {
        self.baseURL = baseURL
        self.token = token
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 180
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.waitsForConnectivity = false
        self.session = session ?? URLSession(configuration: configuration)
    }

    func lastFM(_ body: [String: Any]) async throws -> [String: Any] {
        var request = URLRequest(url: endpoint("v1", "lastfm"))
        request.httpMethod = "POST"
        request.timeoutInterval = (body["action"] as? String) == "radio" ? 45 : 15
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: body)
        authorize(&request)
        let (data, response) = try await perform(request)
        try validate(response, data: data)
        guard data.count <= 65536,
              let result = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw APIError.invalidResponse
        }
        return result
    }

    func radio(for track: Track) async throws -> [Track] {
        let result = try await lastFM(["action": "radio", "title": track.title, "artist": track.artist])
        let data = try JSONSerialization.data(withJSONObject: result)
        guard let response = try? JSONDecoder().decode(SearchResponse.self, from: data), response.tracks.count <= 12 else {
            throw APIError.invalidResponse
        }
        return response.tracks
    }

    func health() async throws {
        var request = URLRequest(url: endpoint("v1", "health"))
        request.timeoutInterval = 15
        authorize(&request)
        let (data, response) = try await perform(request)
        try validate(response, data: data)
    }

    func search(_ query: String, limit: Int = 20) async throws -> [Track] {
        guard var components = URLComponents(
            url: endpoint("v1", "search"),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidConfiguration("The search URL is invalid.")
        }
        components.queryItems = [
            URLQueryItem(name: "q", value: query),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        guard let url = components.url else {
            throw APIError.invalidConfiguration("The search URL is invalid.")
        }
        var request = URLRequest(url: url)
        authorize(&request)
        let (data, response) = try await perform(request)
        try validate(response, data: data)
        do {
            return try JSONDecoder().decode(SearchResponse.self, from: data).tracks
        } catch {
            throw APIError.invalidResponse
        }
    }

    func downloadAudio(for track: Track, protectingTrackIDs: Set<String> = []) async throws -> URL {
        let directory = try audioDirectory()
        let destination = directory.appendingPathComponent("\(track.id).m4a")
        let protectedNames = Set([destination.lastPathComponent] + protectingTrackIDs.map { "\($0).m4a" })
        if let attributes = try? FileManager.default.attributesOfItem(atPath: destination.path),
           let size = attributes[.size] as? NSNumber,
           size.intValue > 0, size.intValue <= 48 * 1024 * 1024 {
            try FileManager.default.setAttributes([.modificationDate: Date()], ofItemAtPath: destination.path)
            try Self.trimAudioCache(directory, protecting: protectedNames)
            return destination
        }
        try? FileManager.default.removeItem(at: destination)

        var request = URLRequest(url: endpoint("v1", "tracks", track.id, "audio"))
        request.timeoutInterval = 180
        authorize(&request)
        request.setValue("audio/mp4", forHTTPHeaderField: "Accept")

        let downloadResult: (URL, URLResponse)
        do {
            downloadResult = try await session.download(for: request)
        } catch let error as URLError {
            throw APIError.transport(message(for: error))
        } catch {
            throw APIError.transport("Could not download the audio.")
        }
        let temporary = downloadResult.0
        defer { try? FileManager.default.removeItem(at: temporary) }
        let response = downloadResult.1
        try Task.checkCancellation()

        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }
        if http.statusCode != 200 {
            let data = (try? Data(contentsOf: temporary, options: .mappedIfSafe))
                .flatMap { $0.count < 16_384 ? $0 : nil }
            throw serverError(status: http.statusCode, data: data)
        }
        let contentType = http.value(forHTTPHeaderField: "Content-Type")?.lowercased() ?? ""
        guard contentType.hasPrefix("audio/mp4") else {
            throw APIError.invalidResponse
        }
        try? FileManager.default.removeItem(at: destination)
        do {
            try FileManager.default.moveItem(at: temporary, to: destination)
        } catch {
            throw APIError.transport("Could not save the downloaded audio.")
        }
        let size = (try? destination.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
        guard size > 0 else {
            try? FileManager.default.removeItem(at: destination)
            throw APIError.invalidResponse
        }
        guard size <= 48 * 1024 * 1024 else {
            try? FileManager.default.removeItem(at: destination)
            throw APIError.fileTooLarge
        }
        try Self.trimAudioCache(directory, protecting: protectedNames)
        return destination
    }

    static func trimAudioCache(_ directory: URL, protecting names: Set<String>,
                               maxBytes: Int = 512 * 1024 * 1024) throws {
        let files = try FileManager.default.contentsOfDirectory(
            at: directory, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey]
        ).filter { $0.pathExtension == "m4a" }.map { url in
            let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
            return (url: url, size: values.fileSize ?? 0, date: values.contentModificationDate ?? .distantPast)
        }.sorted { $0.date < $1.date }
        var total = files.reduce(0) { $0 + $1.size }
        for file in files where total > maxBytes && !names.contains(file.url.lastPathComponent) {
            try FileManager.default.removeItem(at: file.url)
            total -= file.size
        }
    }

    func previewPlaylist(url: String, limit: Int = 50) async throws -> PlaylistPage {
        guard var components = URLComponents(
            url: endpoint("v1", "playlists", "preview"),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidConfiguration("The playlist URL is invalid.")
        }
        components.queryItems = [
            URLQueryItem(name: "url", value: url),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        guard let requestURL = components.url else {
            throw APIError.invalidConfiguration("The playlist URL is invalid.")
        }
        var request = URLRequest(url: requestURL)
        request.timeoutInterval = 90
        authorize(&request)
        return try await decode(PlaylistPage.self, from: request)
    }

    func playlistPage(playlistID: String, offset: Int, limit: Int = 100) async throws -> PlaylistPage {
        guard var components = URLComponents(
            url: endpoint("v1", "playlists", playlistID),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidConfiguration("The playlist URL is invalid.")
        }
        components.queryItems = [
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "limit", value: String(limit))
        ]
        guard let requestURL = components.url else {
            throw APIError.invalidConfiguration("The playlist URL is invalid.")
        }
        var request = URLRequest(url: requestURL)
        request.timeoutInterval = 90
        authorize(&request)
        return try await decode(PlaylistPage.self, from: request)
    }

    func libraryPlaylists() async throws -> [SharedPlaylist] {
        var request = URLRequest(url: endpoint("v1", "library", "playlists"))
        authorize(&request)
        return try await decode(SharedPlaylistList.self, from: request).playlists
    }

    func libraryPlaylist(id: String) async throws -> SharedPlaylist {
        var request = URLRequest(url: endpoint("v1", "library", "playlists", id))
        authorize(&request)
        let playlist = try await decode(SharedPlaylist.self, from: request)
        guard playlist.id == id, let entries = playlist.entries, entries.count <= 5000,
              Set(entries.map(\.id)).count == entries.count else { throw APIError.invalidResponse }
        return playlist
    }

    func mutateLibrary(_ mutation: LibraryMutation) async throws -> LibraryReceipt {
        let url = mutation.operation == .create
            ? endpoint("v1", "library", "playlists")
            : endpoint("v1", "library", "playlists", mutation.playlistID)
        var request = URLRequest(url: url)
        switch mutation.operation {
        case .create: request.httpMethod = "POST"
        case .update: request.httpMethod = "PUT"
        case .delete: request.httpMethod = "DELETE"
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        request.httpBody = try encoder.encode(mutation.body)
        guard let size = request.httpBody?.count, size <= 8 * 1024 * 1024 else {
            throw APIError.invalidConfiguration("This playlist is too large to save.")
        }
        request.setValue(String(size), forHTTPHeaderField: "Content-Length")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        authorize(&request)
        let receipt = try await decode(LibraryReceipt.self, from: request)
        guard receipt.id == mutation.playlistID,
              receipt.revision == (mutation.body.expectedRevision ?? 0) + 1,
              receipt.deleted == (mutation.operation == .delete) else { throw APIError.invalidResponse }
        return receipt
    }

    func lyrics(for track: Track) async throws -> LyricsResponse {
        guard var components = URLComponents(
            url: endpoint("v1", "tracks", track.id, "lyrics"),
            resolvingAgainstBaseURL: false
        ) else {
            throw APIError.invalidConfiguration("The lyrics URL is invalid.")
        }
        components.queryItems = [
            URLQueryItem(name: "title", value: track.title),
            URLQueryItem(name: "artist", value: track.artist),
            URLQueryItem(name: "album", value: track.album),
            URLQueryItem(name: "durationMs", value: String(track.durationMs))
        ]
        guard let requestURL = components.url else {
            throw APIError.invalidConfiguration("The lyrics URL is invalid.")
        }
        var request = URLRequest(url: requestURL)
        request.timeoutInterval = 30
        authorize(&request)
        return try await decode(LyricsResponse.self, from: request)
    }

    private func decode<T: Decodable>(_ type: T.Type, from request: URLRequest) async throws -> T {
        let (data, response) = try await perform(request)
        try validate(response, data: data)
        do {
            return try JSONDecoder().decode(type, from: data)
        } catch {
            throw APIError.invalidResponse
        }
    }

    private func endpoint(_ components: String...) -> URL {
        components.reduce(baseURL) { partial, component in
            partial.appendingPathComponent(component)
        }
    }

    private func authorize(_ request: inout URLRequest) {
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
    }

    private func audioDirectory() throws -> URL {
        guard let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else {
            throw APIError.invalidResponse
        }
        let directory = caches.appendingPathComponent("EmberAudio", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        } catch {
            throw APIError.transport("Could not create the local audio cache.")
        }
        return directory
    }

    private func perform(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        do {
            let (data, response) = try await session.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                throw APIError.invalidResponse
            }
            return (data, http)
        } catch let error as APIError {
            throw error
        } catch let error as URLError {
            throw APIError.transport(message(for: error))
        } catch {
            throw APIError.transport("Could not reach the Ember server.")
        }
    }

    private func validate(_ response: HTTPURLResponse, data: Data) throws {
        guard (200..<300).contains(response.statusCode) else {
            throw serverError(status: response.statusCode, data: data)
        }
    }

    private func serverError(status: Int, data: Data?) -> APIError {
        if status == 401 {
            return .unauthorized
        }
        if let data,
           let body = try? JSONDecoder().decode(APIErrorBody.self, from: data),
           !body.error.message.isEmpty {
            return .server(status, body.error.message)
        }
        return .server(status, nil)
    }

    private func message(for error: URLError) -> String {
        switch error.code {
        case .notConnectedToInternet, .networkConnectionLost:
            return "No network connection. Check Tailscale and try again."
        case .cannotFindHost, .cannotConnectToHost, .dnsLookupFailed:
            return "Could not reach the Ember server. Check the URL and Tailscale."
        case .timedOut:
            return "The Ember server took too long to respond."
        case .secureConnectionFailed, .serverCertificateUntrusted,
             .serverCertificateHasBadDate, .serverCertificateNotYetValid:
            return "The HTTPS certificate was not trusted."
        case .cancelled:
            return "The request was cancelled."
        default:
            return "Could not reach the Ember server."
        }
    }
}
