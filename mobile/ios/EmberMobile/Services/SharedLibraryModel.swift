import Foundation
import Combine
import CryptoKit

@MainActor
final class SharedLibraryModel: ObservableObject {
    @Published private(set) var playlists: [SharedPlaylist] = []
    @Published private(set) var details: [String: SharedPlaylist] = [:]
    @Published private(set) var pending: LibraryMutation?
    @Published private(set) var isOnline = false
    @Published private(set) var isRefreshing = false
    @Published private(set) var isSaving = false
    @Published private(set) var loadingDetails: Set<String> = []
    @Published private(set) var errorMessage: String?

    private var api: APIClient?
    private var epoch = UUID()
    private var cacheURL: URL?
    private let cacheDirectory: URL?

    var canEdit: Bool { isOnline && !isSaving && pending == nil }

    init(cacheDirectory: URL? = nil) {
        self.cacheDirectory = cacheDirectory
    }

    func configure(_ api: APIClient?) {
        epoch = UUID()
        self.api = api
        playlists = []
        details = [:]
        pending = nil
        isOnline = false
        isRefreshing = false
        isSaving = false
        loadingDetails = []
        errorMessage = nil
        cacheURL = nil
        guard let api else { return }
        do {
            let directory: URL
            if let cacheDirectory {
                directory = cacheDirectory
            } else {
                directory = try FileManager.default.url(for: .applicationSupportDirectory,
                    in: .userDomainMask, appropriateFor: nil, create: true)
                    .appendingPathComponent("Ember/SharedLibrary", isDirectory: true)
            }
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let digest = SHA256.hash(data: Data(api.baseURL.absoluteString.utf8))
                .map { String(format: "%02x", $0) }.joined()
            let url = directory.appendingPathComponent("\(digest).json")
            cacheURL = url
            if FileManager.default.fileExists(atPath: url.path) {
                let state = try JSONDecoder().decode(DiskState.self, from: Data(contentsOf: url))
                playlists = state.playlists
                details = state.details
                pending = state.pending
            }
        } catch {
            // Never discard an unreadable pending save and then allow fresh edits.
            cacheURL = nil
            errorMessage = "The private playlist cache could not be opened. Reconnect after checking device storage."
        }
    }

    func refresh() async {
        guard let api, !isRefreshing, !isSaving else { return }
        let ticket = epoch
        isRefreshing = true
        defer { if epoch == ticket { isRefreshing = false } }
        do {
            let result = try await api.libraryPlaylists()
            guard epoch == ticket else { return }
            playlists = result
            let ids = Set(result.map(\.id))
            details = details.filter { ids.contains($0.key) }
            isOnline = true
            errorMessage = nil
            try saveCache()
        } catch {
            guard epoch == ticket else { return }
            isOnline = false
            if case APIError.server(404, _) = error {
                errorMessage = "Shared playlists need Ember backend 0.3.0 or newer. Update the laptop server."
            } else {
                errorMessage = error.localizedDescription
            }
        }
    }

    @discardableResult
    func load(_ id: String) async -> SharedPlaylist? {
        guard let api, !loadingDetails.contains(id), !isSaving else { return nil }
        let ticket = epoch
        loadingDetails.insert(id)
        defer { if epoch == ticket { loadingDetails.remove(id) } }
        do {
            let result = try await api.libraryPlaylist(id: id)
            guard epoch == ticket else { return nil }
            details[id] = result
            isOnline = true
            errorMessage = nil
            try saveCache()
            return result
        } catch {
            guard epoch == ticket else { return nil }
            isOnline = false
            errorMessage = error.localizedDescription
            if case APIError.server(404, _) = error { details.removeValue(forKey: id) }
            return nil
        }
    }

    func submit(_ mutation: LibraryMutation) async -> Bool {
        guard canEdit, cacheURL != nil else { return false }
        pending = mutation
        do {
            // Persist the exact request before sending; timeout/relaunch retries keep IDs.
            try saveCache()
        } catch {
            pending = nil
            errorMessage = "Could not save a private retry record. Free device storage before editing."
            return false
        }
        return await retryPending()
    }

    func retryPending() async -> Bool {
        guard let api, let mutation = pending, !isSaving else { return false }
        epoch = UUID() // Ignore reads that began before this mutation.
        let ticket = epoch
        isRefreshing = false
        loadingDetails = []
        isSaving = true
        errorMessage = nil
        do {
            _ = try await api.mutateLibrary(mutation)
            guard epoch == ticket else { return false }
            pending = nil
            do {
                try saveCache()
            } catch {
                pending = mutation
                isSaving = false
                errorMessage = "Saved on the server, but the private retry record could not be cleared. Retry when device storage is available."
                return false
            }
            isSaving = false
            if mutation.operation == .delete {
                details.removeValue(forKey: mutation.playlistID)
            }
            await refresh()
            if mutation.operation != .delete && isOnline { await load(mutation.playlistID) }
            return true // The mutation was acknowledged even if a subsequent refresh failed.
        } catch {
            guard epoch == ticket else { return false }
            isSaving = false
            isOnline = false
            switch error {
            case APIError.server(let status, _) where [400, 401, 404, 409, 413, 415].contains(status):
                pending = nil // These responses explicitly reject the mutation.
                do { try saveCache() } catch { pending = mutation }
                if status == 409 {
                    errorMessage = "This playlist changed on another device. Reload it, then make your edit again."
                } else {
                    errorMessage = error.localizedDescription
                }
            case APIError.invalidConfiguration:
                pending = nil
                do { try saveCache() } catch { pending = mutation }
                errorMessage = error.localizedDescription
            default:
                // Transport, malformed success and 5xx are ambiguous: retain the exact save.
                errorMessage = "Save not confirmed. Retry pending save before making more edits. " + error.localizedDescription
            }
            return false
        }
    }

    private struct DiskState: Codable {
        let playlists: [SharedPlaylist]
        let details: [String: SharedPlaylist]
        let pending: LibraryMutation?
    }

    private func saveCache() throws {
        guard var url = cacheURL else { throw CocoaError(.fileWriteNoPermission) }
        let data = try JSONEncoder().encode(DiskState(playlists: playlists, details: details, pending: pending))
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
        #else
        try data.write(to: url, options: .atomic)
        #endif
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try url.setResourceValues(values)
    }
}
