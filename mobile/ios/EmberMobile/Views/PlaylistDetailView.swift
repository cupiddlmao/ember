import SwiftUI

struct IndexedTrack: Identifiable {
    let index: Int
    let track: Track
    var id: Int { index }
}

struct PlaylistDetailView: View {
    let playlist: SavedPlaylist

    @State private var addingTrack: Track?
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var tracks: [Track] = []
    @State private var expectedCount = 0
    @State private var isLoading = true
    @State private var hasLoadedAll = false
    @State private var errorMessage: String?
    @State private var searchText = ""

    var body: some View {
        ZStack {
            EmberTheme.backgroundGradient.ignoresSafeArea()

            ScrollView {
                LazyVStack(alignment: .leading, spacing: 18) {
                    header
                    actionButtons

                    if isLoading {
                        loadingState
                    } else if let errorMessage {
                        errorState(errorMessage)
                    } else if tracks.isEmpty {
                        emptyState
                    } else {
                        searchField
                        if playlist.unavailableCount > 0 {
                            Text("\(playlist.unavailableCount) unavailable track\(playlist.unavailableCount == 1 ? " is" : "s are") not included.")
                                .font(.system(size: 12))
                                .foregroundStyle(EmberTheme.pinkSoft)
                                .padding(.horizontal, 20)
                        }
                        trackList
                    }
                }
                .padding(.bottom, 24)
            }
        }
        .navigationTitle(playlist.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(EmberTheme.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button(role: .destructive) {
                        model.removePlaylist(playlist)
                        dismiss()
                    } label: {
                        Label("Remove from Library", systemImage: "trash")
                    }
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .foregroundStyle(EmberTheme.secondaryText)
                }
                .accessibilityLabel("Playlist actions")
            }
        }
        .sheet(item: $addingTrack) { track in AddToPlaylistView(track: track) }
        .task { await loadAll() }
    }

    private var header: some View {
        HStack(alignment: .bottom, spacing: 16) {
            ArtworkView(url: playlist.artwork, size: 132, corner: 20)
                .shadow(color: EmberTheme.pink.opacity(0.2), radius: 20, y: 8)
            VStack(alignment: .leading, spacing: 5) {
                Text("PLAYLIST")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(1.8)
                    .foregroundStyle(EmberTheme.faintText)
                Text(playlist.title)
                    .font(.system(size: 24, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(3)
                if !playlist.owner.isEmpty {
                    Text(playlist.owner)
                        .font(.system(size: 13))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .lineLimit(1)
                }
                Text("\(playlist.trackCount) track\(playlist.trackCount == 1 ? "" : "s")")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(EmberTheme.faintText)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 20)
        .padding(.top, 8)
    }

    private var actionButtons: some View {
        HStack(spacing: 10) {
            Button {
                model.playAll(tracks)
            } label: {
                Label("Play All", systemImage: "play.fill")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.black.opacity(0.82))
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(EmberTheme.accentGradient)
                    .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .disabled(tracks.isEmpty || !hasLoadedAll)

            Button {
                model.shufflePlay(tracks)
            } label: {
                Label("Shuffle", systemImage: "shuffle")
                    .font(.system(size: 14, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 12)
                    .background(EmberTheme.surfaceRaised)
                    .clipShape(Capsule())
            }
            .buttonStyle(.plain)
            .disabled(tracks.isEmpty || !hasLoadedAll)
        }
        .padding(.horizontal, 20)
    }

    private var loadingState: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                ProgressView().tint(EmberTheme.pink)
                Text("Loading every track…")
                    .font(.system(size: 14, weight: .medium))
                    .foregroundStyle(.white)
            }
            if expectedCount > 0 {
                ProgressView(value: Double(tracks.count), total: Double(expectedCount))
                    .tint(EmberTheme.pink)
                Text("\(tracks.count) of \(expectedCount)")
                    .font(.system(size: 11, design: .monospaced))
                    .foregroundStyle(EmberTheme.faintText)
            }
            Text("Play All and Shuffle need the complete list, so Ember loads all pages before enabling them.")
                .font(.system(size: 12))
                .foregroundStyle(EmberTheme.secondaryText)
        }
        .padding(16)
        .emberCard()
        .padding(.horizontal, 20)
    }

    private func errorState(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Playlist unavailable", systemImage: "exclamationmark.triangle.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(EmberTheme.pinkSoft)
            Text(message)
                .font(.system(size: 13))
                .foregroundStyle(EmberTheme.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
            Button("Try again") {
                Task { await loadAll() }
            }
            .font(.system(size: 13, weight: .semibold))
            .foregroundStyle(EmberTheme.pink)
        }
        .padding(16)
        .emberCard()
        .padding(.horizontal, 20)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "music.note")
                .font(.system(size: 30, weight: .light))
                .foregroundStyle(EmberTheme.faintText)
            Text("This playlist has no available tracks")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(.white)
            Text("It may be empty, private, or region restricted.")
                .font(.system(size: 12))
                .foregroundStyle(EmberTheme.secondaryText)
        }
        .frame(maxWidth: .infinity)
        .padding(24)
    }

    private var searchField: some View {
        HStack(spacing: 9) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 14))
                .foregroundStyle(EmberTheme.faintText)
            TextField("Search this playlist", text: $searchText)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .foregroundStyle(.white)
                .tint(EmberTheme.pink)
            if !searchText.isEmpty {
                Button {
                    searchText = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(EmberTheme.faintText)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear playlist search")
            }
        }
        .padding(.horizontal, 12)
        .frame(height: 42)
        .background(EmberTheme.surface)
        .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(EmberTheme.hairline, lineWidth: 1)
        )
        .padding(.horizontal, 20)
    }

    private var trackList: some View {
        LazyVStack(spacing: 4) {
            if visibleTracks.isEmpty {
                Text("No tracks match “\(searchText)”.")
                    .font(.system(size: 13))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .frame(maxWidth: .infinity)
                    .padding(24)
            } else {
                ForEach(visibleTracks) { item in
                    PlaylistTrackRow(
                        track: item.track,
                        number: item.index + 1,
                        isPreparing: model.preparingTrackID == item.track.id
                    ) {
                        model.play(tracks: tracks, startingAt: item.index)
                    }
                    .contextMenu {
                        SongRadioButton(track: item.track)
                        Button("Add to playlist") { addingTrack = item.track }
                        Button {
                            model.playNext(item.track)
                        } label: {
                            Label("Play Next", systemImage: "text.line.first.and.arrowtriangle.forward")
                        }
                        Button {
                            model.addToQueue(item.track)
                        } label: {
                            Label("Add to Queue", systemImage: "text.append")
                        }
                    }
                    .padding(.horizontal, 12)
                }
            }
        }
        .padding(.horizontal, 8)
    }

    private var visibleTracks: [IndexedTrack] {
        tracks.enumerated().compactMap { index, track in
            track.matchesPlaylistSearch(searchText) ? IndexedTrack(index: index, track: track) : nil
        }
    }

    @MainActor
    private func loadAll() async {
        isLoading = true
        hasLoadedAll = false
        errorMessage = nil
        tracks = []
        expectedCount = playlist.trackCount
        var offset = 0
        do {
            while true {
                try Task.checkCancellation()
                let page = try await model.playlistPage(id: playlist.id, offset: offset)
                if expectedCount == 0 {
                    expectedCount = page.playlist.trackCount
                }
                if !page.tracks.isEmpty {
                    tracks.append(contentsOf: page.tracks)
                }
                offset += page.tracks.count
                if !page.hasMore {
                    guard offset == page.total else { throw APIError.invalidResponse }
                    break
                }
                guard !page.tracks.isEmpty else { throw APIError.invalidResponse }
            }
            hasLoadedAll = true
            isLoading = false
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Could not load this playlist."
            isLoading = false
        }
    }
}

private struct PlaylistTrackRow: View {
    let track: Track
    let number: Int
    let isPreparing: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 11) {
                Text("\(number)")
                    .font(.system(size: 11, weight: .medium, design: .monospaced))
                    .foregroundStyle(EmberTheme.faintText)
                    .frame(width: 24, alignment: .trailing)
                ArtworkView(url: track.artworkUrl, size: 46, corner: 8)
                VStack(alignment: .leading, spacing: 2) {
                    Text(track.title)
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                    Text(track.artist)
                        .font(.system(size: 12))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                if isPreparing {
                    ProgressView().tint(EmberTheme.pink).scaleEffect(0.8)
                } else {
                    Text(track.durationText)
                        .font(.system(size: 11, weight: .medium, design: .monospaced))
                        .foregroundStyle(EmberTheme.faintText)
                }
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
