import SwiftUI

struct LyricsView: View {
    let track: Track
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var lyrics: LyricsResponse?
    @State private var isLoading = true
    @State private var errorMessage: String?
    @State private var following = true
    @State private var activeIndex: Int?
    @State private var retryToken = 0

    var body: some View {
        ZStack {
            backdrop

            ScrollViewReader { proxy in
                ZStack(alignment: .bottom) {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 18) {
                            content
                            footer
                        }
                        .padding(.horizontal, 26)
                        .padding(.top, 18)
                        .padding(.bottom, 100)
                    }
                    .scrollIndicators(.hidden)
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 8)
                            .onChanged { _ in
                                if lyrics?.kind == "synced" {
                                    following = false
                                }
                            }
                    )
                    .onChange(of: activeIndex) { _, value in
                        guard following, let value else { return }
                        withAnimation(.easeInOut(duration: 0.45)) {
                            proxy.scrollTo(value, anchor: .center)
                        }
                    }
                    .onChange(of: player.currentTime) { _, value in
                        updateActive(value)
                    }

                    if !following, lyrics?.kind == "synced" {
                        Button {
                            following = true
                            if let activeIndex {
                                withAnimation(.easeInOut(duration: 0.4)) {
                                    proxy.scrollTo(activeIndex, anchor: .center)
                                }
                            }
                        } label: {
                            Label("Resume following", systemImage: "arrow.down.to.line")
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 16)
                                .padding(.vertical, 11)
                                .background(.ultraThinMaterial)
                                .clipShape(Capsule())
                                .overlay(Capsule().stroke(Color.white.opacity(0.16), lineWidth: 1))
                        }
                        .buttonStyle(.plain)
                        .padding(.bottom, 22)
                    }
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
                .padding(.horizontal, 26)
                .padding(.vertical, 14)
                .background(EmberTheme.background.opacity(0.95))
        }
        .preferredColorScheme(.dark)
        .task(id: retryToken) {
            await loadLyrics()
        }
    }

    private var backdrop: some View {
        ZStack {
            LinearGradient(
                colors: [
                    Color.black.opacity(0.55),
                    EmberTheme.background.opacity(0.9),
                    EmberTheme.background
                ],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        }
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text("LYRICS")
                    .font(.system(size: 10, weight: .bold))
                    .tracking(2.1)
                    .foregroundStyle(EmberTheme.faintText)
                Text(track.title)
                    .font(.system(size: 21, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                Text(track.artist)
                    .font(.system(size: 13, weight: .medium))
                    .foregroundStyle(EmberTheme.pinkSoft)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            Button {
                dismiss()
            } label: {
                Image(systemName: "chevron.down")
                    .font(.system(size: 14, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(Color.white.opacity(0.1))
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Close lyrics")
        }
        .padding(.bottom, 8)
    }

    @ViewBuilder
    private var content: some View {
        if isLoading {
            HStack(spacing: 12) {
                ProgressView().tint(EmberTheme.pink)
                Text("Looking for lyrics…")
                    .font(.system(size: 14))
                    .foregroundStyle(EmberTheme.secondaryText)
            }
            .padding(.vertical, 40)
        } else if let errorMessage {
            VStack(alignment: .leading, spacing: 12) {
                Label("Lyrics unavailable", systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(EmberTheme.pinkSoft)
                Text(errorMessage)
                    .font(.system(size: 13))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .fixedSize(horizontal: false, vertical: true)
                Button("Try again") {
                    retryToken += 1
                }
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(EmberTheme.pink)
            }
            .padding(.vertical, 28)
        } else if let lyrics {
            if lyrics.kind == "instrumental" {
                stateCard(
                    icon: "waveform",
                    title: "Instrumental",
                    message: lyrics.message ?? "This recording has no lyrics."
                )
            } else if lyrics.kind == "plain" {
                if !lyrics.lines.isEmpty {
                    Text(lyrics.message ?? "These lyrics are not synchronized.")
                        .font(.system(size: 12))
                        .foregroundStyle(EmberTheme.faintText)
                        .padding(.bottom, 4)
                    ForEach(lyrics.lines.indices, id: \.self) { index in
                        lineLabel(lyrics.lines[index], index: index)
                    }
                } else {
                    stateCard(
                        icon: "text.quote",
                        title: "No lyrics available",
                        message: lyrics.message ?? "LRCLIB has no lyrics for this recording."
                    )
                }
            } else if lyrics.kind == "synced" {
                ForEach(lyrics.lines.indices, id: \.self) { index in
                    let line = lyrics.lines[index]
                    if line.isTimed {
                        Button {
                            if let start = line.start {
                                player.seek(to: start)
                                following = true
                                activeIndex = index
                            }
                        } label: {
                            lineLabel(line, index: index)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Seek to \(line.text)")
                        .disabled(!model.isCurrentTrackLoaded || model.queue.current?.track.id != track.id)
                    } else {
                        lineLabel(line, index: index)
                    }
                }
            } else {
                stateCard(
                    icon: "text.quote",
                    title: "No lyrics available",
                    message: lyrics.message ?? "LRCLIB has no lyrics for this recording."
                )
            }
        }
    }

    private func lineLabel(_ line: LyricsLine, index: Int) -> some View {
        let active = activeIndex == index
        return styledText(line, active: active)
            .font(.system(size: active ? 30 : 23, weight: active ? .bold : .semibold, design: .rounded))
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.vertical, 3)
            .contentShape(Rectangle())
            .animation(.easeInOut(duration: 0.24), value: active)
            .id(index)
    }

    private func styledText(_ line: LyricsLine, active: Bool) -> Text {
        guard active, !line.words.isEmpty else {
            return Text(line.text)
                .foregroundColor(active ? .white : Color.white.opacity(0.4))
        }
        var value = Text("")
        for (index, word) in line.words.enumerated() {
            let color: Color = isWordActive(word, at: index, in: line)
                ? EmberTheme.pinkSoft
                : Color.white.opacity(0.9)
            value = value + Text(word.text).foregroundColor(color)
        }
        return value
    }

    private func isWordActive(_ word: LyricsWord, at index: Int, in line: LyricsLine) -> Bool {
        let time = player.currentTime
        guard let start = word.start, start <= time else { return false }
        if let end = word.end {
            return time < end
        }
        if index + 1 < line.words.count, let next = line.words[index + 1].start {
            return time < next
        }
        return true
    }

    private var footer: some View {
        HStack(spacing: 7) {
            Image(systemName: "text.quote")
                .font(.system(size: 10))
            Text(lyrics?.source == "lrclib" ? "Lyrics from LRCLIB" : "Lyrics")
                .font(.system(size: 11))
            if lyrics?.kind == "synced" {
                Text("· timings supplied by the provider")
                    .font(.system(size: 11))
            }
        }
        .foregroundStyle(EmberTheme.faintText)
        .padding(.top, 20)
    }

    private func stateCard(icon: String, title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 28, weight: .light))
                .foregroundStyle(EmberTheme.pink.opacity(0.85))
            Text(title)
                .font(.system(size: 20, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
            Text(message)
                .font(.system(size: 14))
                .foregroundStyle(EmberTheme.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, 30)
    }

    @MainActor
    private func loadLyrics() async {
        isLoading = true
        errorMessage = nil
        lyrics = nil
        activeIndex = nil
        do {
            let result = try await model.lyrics(for: track)
            guard !Task.isCancelled else { return }
            lyrics = result
            updateActive(player.currentTime)
        } catch APIError.server(404, _) {
            guard !Task.isCancelled else { return }
            errorMessage = "This server does not have the lyrics endpoint. Update the Ember backend to version 0.2.0 or later."
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled else { return }
            errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Could not load lyrics for this track."
        }
        isLoading = false
    }

    private func updateActive(_ time: Double) {
        guard lyrics?.kind == "synced", let lines = lyrics?.lines else {
            if activeIndex != nil { activeIndex = nil }
            return
        }
        var selected: Int?
        for (index, line) in lines.enumerated() {
            guard let start = line.start else { continue }
            if start <= time + 0.08 {
                selected = index
            } else {
                break
            }
        }
        if selected != activeIndex {
            activeIndex = selected
        }
    }
}
