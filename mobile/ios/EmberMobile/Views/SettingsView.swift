import Foundation
import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingDisconnect = false
    @State private var lastFMStatus = "Connect once for both desktop and iPhone."
    @State private var lastFMBusy = false
    @State private var lastFMConnected = false
    @State private var lastFMAuthorizing = false
    @Environment(\.openURL) private var openURL

    var body: some View {
        NavigationStack {
            ZStack {
                EmberTheme.backgroundGradient.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        infoCard(
                            title: "SERVER",
                            rows: [
                                ("Address", model.serverURL),
                                ("Status", "Connected")
                            ]
                        )

                        VStack(alignment: .leading, spacing: 12) {
                            Text("LAST.FM").font(.caption.bold())
                            Text(lastFMStatus).font(.caption)
                            if lastFMConnected {
                                Button("Disconnect Last.fm", role: .destructive) { lastFMAction("disconnect") }
                            } else {
                                Button("Connect Last.fm") { lastFMAction("connect") }
                                if lastFMAuthorizing {
                                    Button("Finish browser authorization") { lastFMAction("finish") }
                                }
                            }
                        }
                        .disabled(lastFMBusy)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .emberCard()

                        infoCard(
                            title: "APP",
                            rows: [
                                ("Version", appVersion),
                                ("Build", appBuild),
                                ("Commit", appCommit)
                            ]
                        )

                        VStack(alignment: .leading, spacing: 8) {
                            Text("PRIVACY")
                                .font(.system(size: 10, weight: .bold))
                                .tracking(1.4)
                                .foregroundStyle(EmberTheme.faintText)
                            Text("The access key is stored in the iPhone Keychain and sent only to this server in an Authorization header. Audio is prepared on your server and downloaded directly over HTTPS. Playlist links and lyrics requests go only to this server. No Google account is connected.")
                                .font(.system(size: 13))
                                .foregroundStyle(EmberTheme.secondaryText)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(16)
                        .emberCard()

                        Button(role: .destructive) {
                            confirmingDisconnect = true
                        } label: {
                            Text("Forget key & disconnect")
                                .font(.system(size: 15, weight: .semibold))
                                .foregroundStyle(EmberTheme.pinkSoft)
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 14)
                                .background(EmberTheme.surface)
                                .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
                                .overlay(
                                    RoundedRectangle(cornerRadius: 14, style: .continuous)
                                        .stroke(EmberTheme.hairline, lineWidth: 1)
                                )
                        }
                        .buttonStyle(.plain)

                        Text("Ember Mobile · Public search, saved public/unlisted playlists, queue playback, and LRCLIB lyrics. No Google account sync or Apple Music lyrics.")
                            .font(.system(size: 11))
                            .foregroundStyle(EmberTheme.faintText)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.horizontal, 4)
                    }
                    .padding(20)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        dismiss()
                    }
                    .foregroundStyle(EmberTheme.pink)
                }
            }
            .toolbarBackground(EmberTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .task { lastFMAction("status") }
        .preferredColorScheme(.dark)
        .confirmationDialog(
            "Forget the server access key?",
            isPresented: $confirmingDisconnect,
            titleVisibility: .visible
        ) {
            Button("Forget key & disconnect", role: .destructive) {
                model.disconnect()
                dismiss()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("Search and playback will stop until you reconnect.")
        }
    }

    private func lastFMAction(_ action: String) {
        guard !lastFMBusy, let api = model.client else { return }
        lastFMBusy = true
        Task {
            defer { lastFMBusy = false }
            do {
                let result = try await api.lastFM(["action": action])
                if let raw = result["url"] as? String, let url = URL(string: raw),
                   url.scheme == "https", url.host == "www.last.fm" {
                    lastFMAuthorizing = true
                    lastFMConnected = false
                    openURL(url)
                    lastFMStatus = "Approve in your browser, then tap Finish browser authorization."
                } else {
                    let status = try await api.lastFM(["action": "status"])
                    lastFMConnected = status["connected"] as? Bool == true
                    if lastFMConnected {
                        lastFMAuthorizing = false
                        lastFMStatus = "Connected as \(status["username"] as? String ?? "") · \(status["pending"] as? Int ?? 0) pending"
                    } else {
                        if action == "disconnect" { lastFMAuthorizing = false }
                        if status["configured"] as? Bool == true {
                            lastFMStatus = "Not connected."
                        } else {
                            lastFMStatus = "Set LASTFM_API_KEY and LASTFM_API_SECRET on the server first."
                        }
                    }
                }
            } catch { lastFMStatus = error.localizedDescription }
        }
    }

    private var appVersion: String {
        bundleString("CFBundleShortVersionString") ?? "Unknown"
    }

    private var appBuild: String {
        bundleString("CFBundleVersion") ?? "Unknown"
    }

    private var appCommit: String {
        guard let value = bundleString("EmberGitCommit"),
              value.lowercased() != "local",
              !value.hasPrefix("$(") else {
            return "Local build"
        }
        return value
    }

    private func bundleString(_ key: String) -> String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: key) as? String else {
            return nil
        }
        let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    private func infoCard(title: String, rows: [(String, String)]) -> some View {
        VStack(alignment: .leading, spacing: 13) {
            Text(title)
                .font(.system(size: 10, weight: .bold))
                .tracking(1.4)
                .foregroundStyle(EmberTheme.faintText)
            ForEach(rows.indices, id: \.self) { index in
                HStack(alignment: .top, spacing: 18) {
                    Text(rows[index].0)
                        .font(.system(size: 13))
                        .foregroundStyle(EmberTheme.secondaryText)
                    Spacer(minLength: 8)
                    Text(rows[index].1)
                        .font(.system(size: 13, weight: .medium))
                        .foregroundStyle(.white)
                        .multilineTextAlignment(.trailing)
                        .lineLimit(2)
                }
            }
        }
        .padding(16)
        .emberCard()
    }
}
