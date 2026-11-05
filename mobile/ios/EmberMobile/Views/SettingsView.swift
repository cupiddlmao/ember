import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingDisconnect = false

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

                        Text("Ember Mobile 0.2.0 · Public search, saved public/unlisted playlists, queue playback, and LRCLIB lyrics. No Google account sync or Apple Music lyrics.")
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
