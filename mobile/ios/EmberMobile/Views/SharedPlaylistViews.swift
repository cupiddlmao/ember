import SwiftUI

struct SharedLibraryStatus: View {
    var onRetrySuccess: (() -> Void)? = nil
    @EnvironmentObject private var library: SharedLibraryModel

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if library.isRefreshing { ProgressView("Refreshing playlists…") }
            if !library.isOnline {
                Label("Cached library • read only until refreshed", systemImage: "wifi.slash")
                    .font(.caption).foregroundStyle(EmberTheme.secondaryText)
            }
            if let message = library.errorMessage {
                Text(message).font(.caption).foregroundStyle(EmberTheme.pinkSoft)
            }
            if library.pending != nil {
                Button("Retry pending save") {
                    Task { if await library.retryPending() { onRetrySuccess?() } }
                }
                    .disabled(library.isSaving)
            }
            Button("Refresh library") { Task { await library.refresh() } }
                .disabled(library.isSaving || library.isRefreshing)
        }
    }
}

struct SharedLibrarySection: View {
    @EnvironmentObject private var library: SharedLibraryModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var creating = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Text("Ember playlists").font(.title2.bold())
                Spacer()
                Button { creating = true } label: { Image(systemName: "plus") }
                    .accessibilityLabel("Create Ember playlist")
                    .disabled(!library.canEdit)
            }
            SharedLibraryStatus()
            if library.playlists.isEmpty && !library.isRefreshing {
                Text("Create a playlist, then add songs from Search. No music account needed.")
                    .foregroundStyle(EmberTheme.secondaryText)
            }
            ForEach(library.playlists) { playlist in
                NavigationLink {
                    SharedPlaylistDetailView(playlistID: playlist.id)
                } label: {
                    HStack(spacing: 12) {
                        Image(systemName: "music.note.list").foregroundStyle(EmberTheme.pink)
                        VStack(alignment: .leading) {
                            Text(playlist.name).font(.headline)
                            Text("\(playlist.trackCount) songs").font(.caption)
                                .foregroundStyle(EmberTheme.secondaryText)
                        }
                        Spacer()
                        Image(systemName: "chevron.right").font(.caption)
                    }.padding(12).emberCard()
                }.buttonStyle(.plain)
            }
        }
        .task { await library.refresh() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await library.refresh() } }
        }
        .sheet(isPresented: $creating) {
            PlaylistNameSheet(title: "New playlist", initialName: "") { name in
                await library.submit(.create(name: name))
            }
        }
    }
}

struct PlaylistNameSheet: View {
    @EnvironmentObject private var library: SharedLibraryModel
    @Environment(\.dismiss) private var dismiss
    let title: String
    let initialName: String
    let save: (String) async -> Bool
    @State private var name = ""
    @State private var working = false

    var body: some View {
        NavigationStack {
            Form {
                TextField("Playlist name", text: $name)
                SharedLibraryStatus(onRetrySuccess: { dismiss() })
            }
            .navigationTitle(title)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }.disabled(working)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        working = true
                        Task {
                            let saved = await save(name.trimmingCharacters(in: .whitespacesAndNewlines))
                            working = false
                            if saved { dismiss() }
                        }
                    }.disabled(working || !library.canEdit || trimmedName.isEmpty || trimmedName.count > 120)
                }
            }
            .onAppear { name = initialName }
            .interactiveDismissDisabled(working)
        }.preferredColorScheme(.dark)
    }

    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
}

struct AddToPlaylistView: View {
    @EnvironmentObject private var library: SharedLibraryModel
    @Environment(\.dismiss) private var dismiss
    let track: Track
    @State private var working = false
    @State private var newName = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Text(track.title).font(.headline)
                    SharedLibraryStatus(onRetrySuccess: { dismiss() })
                }
                Section("Ember playlists") {
                    ForEach(library.playlists) { playlist in
                        Button(playlist.name) {
                            working = true
                            Task {
                                // Always fetch the current revision before adding; never append to stale cache.
                                if let current = await library.load(playlist.id), let entries = current.entries {
                                    let saved = await library.submit(.update(current, name: current.name,
                                        entries: entries + [SharedPlaylistEntry(track: track)]))
                                    if saved { dismiss() }
                                }
                                working = false
                            }
                        }.disabled(working || !library.canEdit || playlist.trackCount >= 5000)
                    }
                }
                Section("New playlist") {
                    TextField("Name", text: $newName)
                    Button("Create and add song") {
                        working = true
                        Task {
                            let saved = await library.submit(.create(
                                name: newName.trimmingCharacters(in: .whitespacesAndNewlines),
                                entries: [SharedPlaylistEntry(track: track)]))
                            working = false
                            if saved { dismiss() }
                        }
                    }.disabled(working || !library.canEdit || newName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || newName.count > 120)
                }
            }
            .navigationTitle("Add to playlist")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") { dismiss() }.disabled(working)
                }
            }
            .task { await library.refresh() }
            .interactiveDismissDisabled(working)
        }.preferredColorScheme(.dark)
    }
}

struct SharedPlaylistDetailView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var library: SharedLibraryModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    let playlistID: String
    @State private var renaming = false
    @State private var deleting = false
    @State private var addingTrack: Track?

    private var playlist: SharedPlaylist? { library.details[playlistID] }
    private var entries: [SharedPlaylistEntry] { playlist?.entries ?? [] }
    private var editable: Bool { library.canEdit && playlist != nil && !library.loadingDetails.contains(playlistID) }

    var body: some View {
        List {
            Section { SharedLibraryStatus() }
            Section {
                Button("Play all") { model.playAll(entries.map(\.track)) }.disabled(entries.isEmpty)
                if entries.isEmpty { Text("No songs yet. Add music from Search.").foregroundStyle(.secondary) }
            }
            Section("Songs") {
                ForEach(Array(entries.enumerated()), id: \.element.id) { index, entry in
                    Button {
                        model.play(tracks: entries.map(\.track), startingAt: index)
                    } label: {
                        HStack(spacing: 12) {
                            ArtworkView(url: entry.track.artworkUrl, size: 44, corner: 8)
                            VStack(alignment: .leading) {
                                Text(entry.track.title).lineLimit(1)
                                Text(entry.track.artist).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                    }
                    .contextMenu {
                        Button("Play next") { model.playNext(entry.track) }
                        Button("Add to queue") { model.addToQueue(entry.track) }
                        Button("Add to playlist") { addingTrack = entry.track }
                    }
                    .deleteDisabled(!editable)
                    .moveDisabled(!editable)
                }
                .onDelete { offsets in
                    var updated = entries
                    updated.remove(atOffsets: offsets)
                    saveEntries(updated)
                }
                .onMove { source, destination in
                    var updated = entries
                    updated.move(fromOffsets: source, toOffset: destination)
                    saveEntries(updated)
                }
            }
        }
        .navigationTitle(playlist?.name ?? "Ember playlist")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) { EditButton().disabled(!editable) }
            ToolbarItem(placement: .topBarTrailing) {
                Menu {
                    Button("Rename") { renaming = true }
                    Button("Delete playlist", role: .destructive) { deleting = true }
                } label: { Image(systemName: "ellipsis.circle") }
                .disabled(!editable)
                .accessibilityLabel("Playlist actions")
            }
        }
        .task { _ = await library.load(playlistID) }
        .refreshable { await library.load(playlistID); await library.refresh() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await library.load(playlistID) } }
        }
        .sheet(isPresented: $renaming) {
            PlaylistNameSheet(title: "Rename playlist", initialName: playlist?.name ?? "") { name in
                guard let playlist, let entries = playlist.entries else { return false }
                return await library.submit(.update(playlist, name: name, entries: entries))
            }
        }
        .sheet(item: $addingTrack) { track in AddToPlaylistView(track: track) }
        .confirmationDialog("Delete this playlist from all devices?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete playlist", role: .destructive) {
                guard let playlist else { return }
                Task { if await library.submit(.delete(playlist)) { dismiss() } }
            }
        }
    }

    private func saveEntries(_ updated: [SharedPlaylistEntry]) {
        guard editable, let playlist else { return }
        Task { _ = await library.submit(.update(playlist, name: playlist.name, entries: updated)) }
    }
}
