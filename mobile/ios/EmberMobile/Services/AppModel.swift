import Foundation
import SwiftUI

@MainActor
final class AppModel: ObservableObject {
    enum Sheet: String, Identifiable {
        case nowPlaying
        case settings

        var id: String { rawValue }
    }

    @Published var serverURL: String
    @Published var accessKey: String
    @Published var searchText = ""
    @Published private(set) var results: [Track] = []
    @Published private(set) var isSearching = false
    @Published private(set) var isConnecting = false
    @Published private(set) var isRestoring = true
    @Published private(set) var connectionError: String?
    @Published private(set) var errorMessage: String?
    @Published private(set) var preparingTrackID: String?
    @Published var activeSheet: Sheet?

    let player = PlayerController()

    @Published private(set) var client: APIClient?
    private var searchTask: Task<Void, Never>?
    private var preparationTask: Task<Void, Never>?
    private var didRestore = false

    private static let serverURLKey = "ember.server.url"

    init() {
        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? ""
        accessKey = ""
    }

    var isConnected: Bool {
        client != nil
    }

    func restoreSession() async {
        guard !didRestore else { return }
        didRestore = true
        defer { isRestoring = false }

        serverURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? ""
        accessKey = KeychainStore.read() ?? ""
        guard !serverURL.isEmpty, !accessKey.isEmpty else { return }

        isConnecting = true
        defer { isConnecting = false }
        do {
            let url = try normalizedServerURL(serverURL)
            let api = APIClient(baseURL: url, token: accessKey)
            try await api.health()
            client = api
            connectionError = nil
        } catch {
            client = nil
            connectionError = "Could not reconnect to the saved server. Check Tailscale, then try again."
        }
    }

    func connect() async {
        guard !isConnecting else { return }
        connectionError = nil
        errorMessage = nil
        isConnecting = true
        defer { isConnecting = false }

        let key = accessKey.trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            let url = try normalizedServerURL(serverURL)
            guard !key.isEmpty else {
                throw APIError.invalidConfiguration("Enter the access key from the server's .env file.")
            }
            let api = APIClient(baseURL: url, token: key)
            try await api.health()
            try KeychainStore.save(key)
            UserDefaults.standard.set(url.absoluteString, forKey: Self.serverURLKey)
            serverURL = url.absoluteString
            accessKey = key
            client = api
            connectionError = nil
        } catch {
            connectionError = (error as? LocalizedError)?.errorDescription
                ?? "Could not connect to the server."
        }
    }

    func disconnect() {
        searchTask?.cancel()
        preparationTask?.cancel()
        preparationTask = nil
        preparingTrackID = nil
        player.stop()
        client = nil
        results = []
        searchText = ""
        isSearching = false
        errorMessage = nil
        connectionError = nil
        KeychainStore.delete()
        accessKey = ""
    }

    func scheduleSearch() {
        searchTask?.cancel()
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.count >= 2 else {
            results = []
            isSearching = false
            return
        }
        isSearching = true
        searchTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 350_000_000)
            guard !Task.isCancelled else { return }
            await self?.performSearch(query)
        }
    }

    func searchNow() {
        let query = searchText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard query.count >= 2 else { return }
        searchTask?.cancel()
        searchTask = Task { await performSearch(query) }
    }

    func play(_ track: Track) {
        guard let api = client else { return }
        preparationTask?.cancel()
        errorMessage = nil
        preparingTrackID = track.id
        preparationTask = Task { [weak self] in
            guard let self else { return }
            do {
                let fileURL = try await api.downloadAudio(for: track, protecting: self.player.track?.id)
                guard !Task.isCancelled, self.client != nil else {
                    return
                }
                self.preparingTrackID = nil
                self.player.play(track: track, fileURL: fileURL)
                self.activeSheet = .nowPlaying
            } catch {
                guard !Task.isCancelled else { return }
                self.preparingTrackID = nil
                self.errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? "Could not play this track."
            }
        }
    }

    func clearError() {
        errorMessage = nil
    }

    private func performSearch(_ query: String) async {
        guard let api = client else { return }
        isSearching = true
        defer {
            if currentQuery == query {
                isSearching = false
            }
        }
        do {
            let tracks = try await api.search(query)
            guard !Task.isCancelled, client != nil, currentQuery == query else { return }
            results = tracks
        } catch {
            guard !Task.isCancelled, client != nil, currentQuery == query else { return }
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Search failed. Try again."
        }
    }

    private var currentQuery: String {
        searchText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func normalizedServerURL(_ raw: String) throws -> URL {
        var trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        while trimmed.hasSuffix("/") {
            trimmed.removeLast()
        }
        guard let components = URLComponents(string: trimmed),
              components.scheme?.lowercased() == "https",
              let host = components.host,
              !host.isEmpty,
              components.user == nil,
              components.password == nil,
              components.query == nil,
              components.fragment == nil,
              let url = components.url else {
            throw APIError.invalidConfiguration(
                "Enter an HTTPS address such as https://ember.your-tailnet.ts.net."
            )
        }
        return url
    }
}
