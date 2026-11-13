import SwiftUI

struct LibraryView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var library: SharedLibraryModel
    @State private var showingAdd = false
    @State private var pendingRemoval: SavedPlaylist?

    var body: some View {
        NavigationStack {
            ZStack {
                EmberTheme.backgroundGradient.ignoresSafeArea()

                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        SharedLibrarySection()
                        Divider().overlay(EmberTheme.hairline)
                        HStack {
                            Text("Saved YouTube links").font(.headline)
                            Spacer()
                            Button("Save link") { showingAdd = true }
                        }
                        Text("These bookmarks stay on this iPhone. Ember playlists above sync through your server.")
                            .font(.caption).foregroundStyle(EmberTheme.secondaryText)
                        ForEach(model.savedPlaylists) { playlist in
                            NavigationLink {
                                PlaylistDetailView(playlist: playlist)
                            } label: {
                                PlaylistRow(playlist: playlist)
                            }
                            .buttonStyle(.plain)
                            .contextMenu {
                                Button("Remove saved link", role: .destructive) { pendingRemoval = playlist }
                            }
                        }
                    }
                    .foregroundStyle(.white)
                    .padding(18)
                }
                .refreshable { await library.refresh() }

            }
            .navigationTitle("Library")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(EmberTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbarColorScheme(.dark, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button {
                        model.activeSheet = .settings
                    } label: {
                        Image(systemName: "gearshape.fill")
                            .foregroundStyle(EmberTheme.secondaryText)
                    }
                    .accessibilityLabel("Settings")
                }
            }
        }
        .sheet(isPresented: $showingAdd) {
            AddPlaylistView()
                .environmentObject(model)
        }
        .confirmationDialog(
            "Remove this playlist?",
            isPresented: Binding(
                get: { pendingRemoval != nil },
                set: { if !$0 { pendingRemoval = nil } }
            ),
            titleVisibility: .visible,
            presenting: pendingRemoval
        ) { playlist in
            Button("Remove from Library", role: .destructive) {
                model.removePlaylist(playlist)
                pendingRemoval = nil
            }
            Button("Cancel", role: .cancel) {
                pendingRemoval = nil
            }
        } message: { _ in
            Text("The playlist stays on YouTube Music. Only the local library reference is removed.")
        }
    }

}

private struct PlaylistRow: View {
    let playlist: SavedPlaylist

    var body: some View {
        HStack(spacing: 14) {
            ArtworkView(url: playlist.artwork, size: 66, corner: 13)
            VStack(alignment: .leading, spacing: 5) {
                Text(playlist.title)
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                if !playlist.owner.isEmpty {
                    Text(playlist.owner)
                        .font(.system(size: 12))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .lineLimit(1)
                }
                Text("\(playlist.trackCount) track\(playlist.trackCount == 1 ? "" : "s")")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(EmberTheme.faintText)
            }
            Spacer(minLength: 6)
            Image(systemName: "chevron.right")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(EmberTheme.faintText)
        }
        .padding(10)
        .background(EmberTheme.surface.opacity(0.86))
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .stroke(EmberTheme.hairline, lineWidth: 1)
        )
        .contentShape(Rectangle())
    }
}

struct AddPlaylistView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool

    @State private var url = ""
    @State private var page: PlaylistPage?
    @State private var errorMessage: String?
    @State private var isPreviewing = false

    var body: some View {
        NavigationStack {
            ZStack {
                EmberTheme.backgroundGradient.ignoresSafeArea()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        Text("Paste a playlist link")
                            .font(.system(size: 22, weight: .bold, design: .rounded))
                            .foregroundStyle(.white)
                        Text("Use a public or unlisted YouTube Music playlist. Single songs, private playlists, and account-library sync are not supported in this build.")
                            .font(.system(size: 13))
                            .foregroundStyle(EmberTheme.secondaryText)
                            .fixedSize(horizontal: false, vertical: true)

                        TextField("https://music.youtube.com/playlist?list=…", text: $url, axis: .vertical)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                            .lineLimit(1...4)
                            .focused($focused)
                            .foregroundStyle(.white)
                            .tint(EmberTheme.pink)
                            .padding(13)
                            .background(EmberTheme.surfaceRaised)
                            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                            .overlay(
                                RoundedRectangle(cornerRadius: 12, style: .continuous)
                                    .stroke(EmberTheme.hairline, lineWidth: 1)
                            )

                        if let errorMessage {
                            Text(errorMessage)
                                .font(.system(size: 13))
                                .foregroundStyle(EmberTheme.pinkSoft)
                                .fixedSize(horizontal: false, vertical: true)
                        }

                        Button {
                            Task { await preview() }
                        } label: {
                            if isPreviewing {
                                ProgressView().tint(.black)
                            } else {
                                Text(page == nil ? "Preview playlist" : "Preview again")
                            }
                        }
                        .buttonStyle(PrimaryButtonStyle())
                        .disabled(isPreviewing || url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                        if let page {
                            previewCard(page)
                        }
                    }
                    .padding(20)
                }
            }
            .navigationTitle("Add Playlist")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                        .foregroundStyle(EmberTheme.pink)
                }
            }
            .toolbarBackground(EmberTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
        }
        .preferredColorScheme(.dark)
    }

    private func previewCard(_ page: PlaylistPage) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 13) {
                ArtworkView(url: page.playlist.artworkUrl, size: 74, corner: 13)
                VStack(alignment: .leading, spacing: 4) {
                    Text(page.playlist.title)
                        .font(.system(size: 17, weight: .bold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                    if !page.playlist.owner.isEmpty {
                        Text(page.playlist.owner)
                            .font(.system(size: 12))
                            .foregroundStyle(EmberTheme.secondaryText)
                            .lineLimit(1)
                    }
                    Text("\(page.playlist.trackCount) available track\(page.playlist.trackCount == 1 ? "" : "s")")
                        .font(.system(size: 11))
                        .foregroundStyle(EmberTheme.faintText)
                }
                Spacer(minLength: 0)
            }

            if page.playlist.unavailableCount > 0 {
                Text("\(page.playlist.unavailableCount) unavailable track\(page.playlist.unavailableCount == 1 ? " is" : "s are") skipped by YouTube Music.")
                    .font(.system(size: 12))
                    .foregroundStyle(EmberTheme.pinkSoft)
            }

            if !page.tracks.isEmpty {
                VStack(alignment: .leading, spacing: 9) {
                    ForEach(page.tracks.prefix(5).indices, id: \.self) { index in
                        let track = page.tracks[index]
                        HStack(spacing: 8) {
                            Text("\(index + 1)")
                                .font(.system(size: 11, weight: .medium, design: .monospaced))
                                .foregroundStyle(EmberTheme.faintText)
                                .frame(width: 18, alignment: .trailing)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(track.title)
                                    .font(.system(size: 13, weight: .medium))
                                    .foregroundStyle(.white)
                                    .lineLimit(1)
                                Text(track.artist)
                                    .font(.system(size: 11))
                                    .foregroundStyle(EmberTheme.secondaryText)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                        }
                    }
                }
            }

            Button {
                model.addPlaylist(page.playlist)
                dismiss()
            } label: {
                Text(model.isSavedPlaylist(page.playlist) ? "Update in Library" : "Save to Library")
            }
            .buttonStyle(PrimaryButtonStyle())
        }
        .padding(15)
        .emberCard()
    }

    @MainActor
    private func preview() async {
        focused = false
        isPreviewing = true
        errorMessage = nil
        defer { isPreviewing = false }
        do {
            page = try await model.previewPlaylist(url: url)
        } catch {
            page = nil
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Could not preview that playlist."
        }
    }
}
