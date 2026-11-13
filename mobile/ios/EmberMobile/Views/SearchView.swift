import SwiftUI

struct SearchView: View {
    @State private var addingTrack: Track?
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(spacing: 0) {
            header
            searchField
            content
        }
        .sheet(item: $addingTrack) { track in AddToPlaylistView(track: track) }
        .onChange(of: model.searchText) { _, _ in
            model.scheduleSearch()
        }
    }

    private var header: some View {
        HStack {
            HStack(spacing: 8) {
                Image(systemName: "flame.fill")
                    .font(.system(size: 22, weight: .bold))
                    .foregroundStyle(EmberTheme.accentGradient)
                Text("Ember")
                    .font(.system(size: 29, weight: .heavy, design: .rounded))
                    .foregroundStyle(.white)
            }
            Spacer()
            Button {
                model.activeSheet = .settings
            } label: {
                Image(systemName: "gearshape.fill")
                    .font(.system(size: 17, weight: .semibold))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .frame(width: 38, height: 38)
                    .background(EmberTheme.surface)
                    .clipShape(Circle())
                    .overlay(Circle().stroke(EmberTheme.hairline, lineWidth: 1))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
    }

    private var searchField: some View {
        HStack(spacing: 10) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(EmberTheme.faintText)
            TextField("Search songs, artists, albums", text: $model.searchText)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.search)
                .onSubmit { model.searchNow() }
                .foregroundStyle(.white)
                .tint(EmberTheme.pink)
            if !model.searchText.isEmpty {
                Button {
                    model.searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 15))
                        .foregroundStyle(EmberTheme.faintText)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 14)
        .frame(height: 46)
        .background(EmberTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(EmberTheme.hairline, lineWidth: 1)
        )
        .padding(.horizontal, 20)
        .padding(.top, 14)
        .padding(.bottom, 10)
    }

    @ViewBuilder
    private var content: some View {
        if model.isSearching && model.results.isEmpty {
            VStack(spacing: 14) {
                ProgressView()
                    .tint(EmberTheme.pink)
                Text("Searching YouTube Music…")
                    .font(.system(size: 14))
                    .foregroundStyle(EmberTheme.secondaryText)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else if model.results.isEmpty {
            emptyState
        } else {
            ScrollView {
                LazyVStack(spacing: 6) {
                    ForEach(model.results) { track in
                        TrackRow(
                            track: track,
                            isPreparing: model.preparingTrackID == track.id
                        ) {
                            model.play(track)
                        }
                        .contextMenu {
                            SongRadioButton(track: track)
                            Button("Add to playlist") { addingTrack = track }
                            Button {
                                model.playNext(track)
                            } label: {
                                Label("Play Next", systemImage: "text.line.first.and.arrowtriangle.forward")
                            }
                            Button {
                                model.addToQueue(track)
                            } label: {
                                Label("Add to Queue", systemImage: "text.append")
                            }
                        }
                        .padding(.horizontal, 12)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.top, 4)
                .padding(.bottom, 18)
            }
            .scrollDismissesKeyboard(.immediately)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 13) {
            Image(systemName: model.searchText.isEmpty ? "flame" : "magnifyingglass")
                .font(.system(size: 38, weight: .light))
                .foregroundStyle(EmberTheme.pink.opacity(0.7))
            Text(model.searchText.isEmpty ? "Find something to play" : "No songs found")
                .font(.system(size: 18, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
            Text(
                model.searchText.isEmpty
                ? "Search public YouTube Music tracks, or save public and unlisted playlists in your Library."
                : "Try a different spelling, song, or artist."
            )
            .font(.system(size: 13))
            .foregroundStyle(EmberTheme.secondaryText)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 46)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
private struct TrackRow: View {
    let track: Track
    let isPreparing: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ArtworkView(url: track.artworkUrl, size: 54, corner: 10)
                VStack(alignment: .leading, spacing: 3) {
                    Text(track.title)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                    Text(track.artist)
                        .font(.system(size: 13))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if isPreparing {
                    ProgressView()
                        .tint(EmberTheme.pink)
                        .scaleEffect(0.8)
                } else {
                    Text(track.durationText)
                        .font(.system(size: 12, weight: .medium, design: .monospaced))
                        .foregroundStyle(EmberTheme.faintText)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
