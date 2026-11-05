import SwiftUI

struct QueueView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var confirmingClear = false

    var body: some View {
        NavigationStack {
            ZStack {
                EmberTheme.backgroundGradient.ignoresSafeArea()

                if model.queue.current == nil && model.queue.upcoming.isEmpty {
                    emptyState
                } else {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 16) {
                            if let current = model.queue.current {
                                sectionTitle("NOW PLAYING")
                                CurrentQueueRow(entry: current)
                            }

                            HStack {
                                sectionTitle("UP NEXT")
                                Spacer()
                                if !model.queue.upcoming.isEmpty {
                                    Button("Clear") {
                                        confirmingClear = true
                                    }
                                    .font(.system(size: 12, weight: .semibold))
                                    .foregroundStyle(EmberTheme.pinkSoft)
                                }
                            }

                            if model.queue.upcoming.isEmpty {
                                Text("Nothing else is queued. Use Play Next or Add to Queue on any song.")
                                    .font(.system(size: 13))
                                    .foregroundStyle(EmberTheme.secondaryText)
                                    .padding(.vertical, 12)
                            } else {
                                ForEach(model.queue.upcoming) { entry in
                                    UpcomingQueueRow(
                                        entry: entry,
                                        index: upcomingIndex(entry),
                                        count: model.queue.upcoming.count
                                    )
                                }
                            }
                        }
                        .padding(.horizontal, 18)
                        .padding(.top, 8)
                        .padding(.bottom, 28)
                    }
                }
            }
            .navigationTitle("Queue")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(EmberTheme.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                        .foregroundStyle(EmberTheme.pink)
                }
            }
        }
        .preferredColorScheme(.dark)
        .confirmationDialog(
            "Clear upcoming tracks?",
            isPresented: $confirmingClear,
            titleVisibility: .visible
        ) {
            Button("Clear Upcoming", role: .destructive) {
                model.clearUpcoming()
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("The current song keeps playing.")
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "list.bullet")
                .font(.system(size: 38, weight: .light))
                .foregroundStyle(EmberTheme.faintText)
            Text("The queue is empty")
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(.white)
            Text("Choose a song or use Play Next and Add to Queue.")
                .font(.system(size: 13))
                .foregroundStyle(EmberTheme.secondaryText)
                .multilineTextAlignment(.center)
        }
        .padding(30)
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text)
            .font(.system(size: 10, weight: .bold))
            .tracking(1.6)
            .foregroundStyle(EmberTheme.faintText)
    }

    private func upcomingIndex(_ entry: QueueEntry) -> Int {
        model.queue.upcoming.firstIndex(of: entry) ?? 0
    }
}

private struct CurrentQueueRow: View {
    let entry: QueueEntry

    var body: some View {
        HStack(spacing: 12) {
            ArtworkView(url: entry.track.artworkUrl, size: 54, corner: 10)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.track.title)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text(entry.track.artist)
                    .font(.system(size: 12))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .lineLimit(1)
            }
            Spacer(minLength: 6)
            Image(systemName: "waveform")
                .font(.system(size: 16, weight: .medium))
                .foregroundStyle(EmberTheme.pink)
        }
        .padding(10)
        .background(EmberTheme.surfaceRaised)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(EmberTheme.pink.opacity(0.22), lineWidth: 1)
        )
    }
}

private struct UpcomingQueueRow: View {
    @EnvironmentObject private var model: AppModel
    let entry: QueueEntry
    let index: Int
    let count: Int

    var body: some View {
        HStack(spacing: 10) {
            Button {
                model.playQueueEntry(entry)
            } label: {
                HStack(spacing: 11) {
                    ArtworkView(url: entry.track.artworkUrl, size: 46, corner: 8)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.track.title)
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                        Text(entry.track.artist)
                            .font(.system(size: 12))
                            .foregroundStyle(EmberTheme.secondaryText)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 4)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Play \(entry.track.title) now")

            VStack(spacing: 2) {
                Button {
                    model.moveUpcoming(entry, offset: -1)
                } label: {
                    Image(systemName: "chevron.up")
                        .font(.system(size: 11, weight: .bold))
                        .frame(width: 28, height: 22)
                }
                .buttonStyle(.plain)
                .foregroundStyle(index > 0 ? EmberTheme.secondaryText : EmberTheme.faintText.opacity(0.35))
                .disabled(index == 0)
                .accessibilityLabel("Move \(entry.track.title) up")

                Button {
                    model.moveUpcoming(entry, offset: 1)
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 11, weight: .bold))
                        .frame(width: 28, height: 22)
                }
                .buttonStyle(.plain)
                .foregroundStyle(index < count - 1 ? EmberTheme.secondaryText : EmberTheme.faintText.opacity(0.35))
                .disabled(index == count - 1)
                .accessibilityLabel("Move \(entry.track.title) down")
            }

            Button {
                model.removeUpcoming(entry)
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(EmberTheme.faintText)
                    .frame(width: 30, height: 38)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Remove \(entry.track.title) from queue")
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
    }
}
