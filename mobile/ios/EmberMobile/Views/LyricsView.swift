import Foundation
import SwiftUI

struct LyricsView: View {
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.verticalSizeClass) private var verticalSizeClass
    @StateObject private var session: LyricsSession
    @State private var retryToken = 0
    @State private var showingFullscreen = false
    @ScaledMetric(relativeTo: .title2) private var lyricSize: CGFloat = 28
    @ScaledMetric(relativeTo: .title2) private var titleSize: CGFloat = 21
    @ScaledMetric(relativeTo: .subheadline) private var artistSize: CGFloat = 13

    init(track: Track, player: PlayerController) {
        _player = ObservedObject(wrappedValue: player)
        _session = StateObject(wrappedValue: LyricsSession(track: track))
    }

    private var synced: Bool {
        session.lyrics?.kind == "synced"
    }

    private var loadKey: String {
        "\(session.track.id)-\(retryToken)"
    }

    var body: some View {
        ZStack {
            LyricsBackdrop(url: session.track.artworkUrl)

            ScrollViewReader { proxy in
                ZStack(alignment: .bottom) {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: verticalSizeClass == .compact ? 12 : 16) {
                            LyricsContentView(
                                session: session,
                                player: player,
                                lyricSize: lyricSize,
                                compact: verticalSizeClass == .compact
                            ) {
                                retryToken += 1
                            }
                        }
                        .padding(.horizontal, 26)
                        .padding(.top, verticalSizeClass == .compact ? 28 : 86)
                        .padding(.bottom, verticalSizeClass == .compact ? 72 : 132)
                    }
                    .scrollIndicators(.hidden)
                    .simultaneousGesture(
                        DragGesture(minimumDistance: 8)
                            .onChanged { _ in
                                if synced && session.following {
                                    session.following = false
                                }
                            }
                    )

                    if !session.following && synced {
                        resumeButton(proxy: proxy)
                            .padding(.bottom, 18)
                    }
                }
                .onAppear {
                    session.sync(time: player.currentTime)
                    if session.following {
                        scrollLyrics(proxy, to: session.activeIndex, animated: false)
                    }
                }
                .onChange(of: session.activeIndex) { _, index in
                    guard session.following, !showingFullscreen else { return }
                    scrollLyrics(proxy, to: index, animated: !reduceMotion)
                }
                .onChange(of: player.currentTime) { _, time in
                    guard !showingFullscreen else { return }
                    session.sync(time: time)
                }
                .onChange(of: showingFullscreen) { _, expanded in
                    guard !expanded else { return }
                    session.sync(time: player.currentTime)
                    if session.following {
                        scrollLyrics(proxy, to: session.activeIndex, animated: !reduceMotion)
                    }
                }
            }
        }
        .safeAreaInset(edge: .top, spacing: 0) {
            header
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            transportBar
        }
        .preferredColorScheme(.dark)
        .task(id: loadKey) {
            await loadLyrics()
        }
        .onChange(of: model.queue.current?.track.id) { _, _ in
            updateTrack()
        }
        .fullScreenCover(isPresented: $showingFullscreen) {
            ImmersiveLyricsView(
                session: session,
                player: player,
                onClose: { showingFullscreen = false },
                onRetry: { retryToken += 1 }
            )
            .environmentObject(model)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 11) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("Lyrics")
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle(EmberTheme.pinkSoft.opacity(0.9))
                    Text(session.track.title)
                        .font(.system(size: titleSize, weight: .bold, design: .rounded))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                    Text(session.track.artist)
                        .font(.system(size: artistSize, weight: .medium))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)

                HStack(spacing: 8) {
                    Button {
                        showingFullscreen = true
                    } label: {
                        Image(systemName: "arrow.up.left.and.arrow.down.right")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundStyle(.white)
                            .frame(width: 36, height: 36)
                            .background(Color.white.opacity(0.08))
                            .clipShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Expand lyrics")
                    .accessibilityHint("Opens lyrics in full screen")

                    Button {
                        dismiss()
                    } label: {
                        Image(systemName: "chevron.down")
                            .font(.system(size: 14, weight: .bold))
                            .foregroundStyle(.white)
                            .frame(width: 36, height: 36)
                            .background(Color.white.opacity(0.08))
                            .clipShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Close lyrics")
                }
            }

            if synced && !session.isLoading {
                timingControls
            }
        }
        .padding(.horizontal, 26)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .background(EmberTheme.background.opacity(0.78))
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(EmberTheme.hairline)
                .frame(height: 1)
        }
    }

    private var timingControls: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Text("Sync offset")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(EmberTheme.faintText)
                Spacer(minLength: 4)
                Text(LyricsTiming.offsetText(session.timingOffset))
                    .font(.system(size: 12, weight: .semibold, design: .monospaced))
                    .foregroundStyle(EmberTheme.pinkSoft)
                Button("Reset") {
                    setTimingOffset(0)
                }
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(session.timingOffset == 0 ? EmberTheme.faintText : EmberTheme.pink)
                .disabled(session.timingOffset == 0)
            }

            HStack(spacing: 7) {
                timingButton(
                    "Earlier",
                    systemImage: "minus",
                    direction: -1,
                    disabled: session.timingOffset <= -LyricsTiming.limit
                )
                timingButton(
                    "Later",
                    systemImage: "plus",
                    direction: 1,
                    disabled: session.timingOffset >= LyricsTiming.limit
                )
                Spacer(minLength: 4)
                Text(LyricsTiming.directionText(session.timingOffset))
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 9)
        .background(Color.white.opacity(0.055))
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(EmberTheme.hairline, lineWidth: 1)
        )
    }

    private func timingButton(
        _ title: String,
        systemImage: String,
        direction: Int,
        disabled: Bool
    ) -> some View {
        Button {
            setTimingOffset(LyricsTiming.stepped(session.timingOffset, by: direction))
        } label: {
            Label(title, systemImage: systemImage)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(.white)
                .padding(.horizontal, 9)
                .frame(height: 28)
                .background(Color.white.opacity(0.08))
                .clipShape(Capsule())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.35 : 1)
        .accessibilityLabel("Move lyrics \(title.lowercased()) by 0.25 seconds")
    }

    private var transportBar: some View {
        VStack(spacing: 0) {
            Rectangle()
                .fill(EmberTheme.hairline)
                .frame(height: 1)
            LyricsTransportControls(player: player)
                .padding(.horizontal, 26)
                .padding(.vertical, 10)
        }
        .background(EmberTheme.background.opacity(0.94))
    }

    private func resumeButton(proxy: ScrollViewProxy) -> some View {
        Button {
            session.following = true
            scrollLyrics(proxy, to: session.activeIndex, animated: !reduceMotion)
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
    }

    @MainActor
    private func loadLyrics() async {
        let requested = session.track
        session.isLoading = true
        session.errorMessage = nil
        session.lyrics = nil
        session.activeIndex = nil
        session.following = true

        do {
            let result = try await model.lyrics(for: requested)
            guard !Task.isCancelled, session.track.id == requested.id else { return }
            session.lyrics = result
            session.isLoading = false
            session.sync(time: player.currentTime)
        } catch APIError.server(404, _) {
            guard !Task.isCancelled, session.track.id == requested.id else { return }
            session.errorMessage = "This server does not have the lyrics endpoint. Update the Ember backend to version 0.2.0 or later."
            session.isLoading = false
        } catch is CancellationError {
            return
        } catch {
            guard !Task.isCancelled, session.track.id == requested.id else { return }
            session.errorMessage = (error as? LocalizedError)?.errorDescription
                ?? "Could not load lyrics for this track."
            session.isLoading = false
        }
    }

    private func updateTrack() {
        guard let next = model.queue.current?.track else { return }
        guard next.id != session.track.id else { return }
        session.replaceTrack(next)
    }

    private func setTimingOffset(_ value: Double) {
        // This only shifts lyric presentation; it never seeks or changes audio playback.
        let normalized = LyricsTiming.normalized(value)
        guard normalized != session.timingOffset else { return }
        session.timingOffset = normalized
        LyricsOffsetStore.set(normalized, for: session.track.id)
        session.sync(time: player.currentTime)
    }
}

final class LyricsSession: ObservableObject {
    @Published var track: Track
    @Published var lyrics: LyricsResponse?
    @Published var isLoading = true
    @Published var errorMessage: String?
    @Published var activeIndex: Int?
    @Published var following = true
    @Published var timingOffset: Double

    init(track: Track) {
        self.track = track
        timingOffset = LyricsOffsetStore.offset(for: track.id)
    }

    func replaceTrack(_ next: Track) {
        guard next.id != track.id else { return }
        track = next
        timingOffset = LyricsOffsetStore.offset(for: next.id)
        lyrics = nil
        isLoading = true
        errorMessage = nil
        activeIndex = nil
        following = true
    }

    func sync(time: Double) {
        let selected: Int?
        if let lyrics {
            selected = LyricsTiming.activeLineIndex(
                in: lyrics.lines,
                playbackTime: time,
                offset: timingOffset
            )
        } else {
            selected = nil
        }
        if selected != activeIndex {
            activeIndex = selected
        }
    }
}

private struct LyricsContentView: View {
    @ObservedObject var session: LyricsSession
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    let lyricSize: CGFloat
    let compact: Bool
    let onRetry: () -> Void

    private var canSeek: Bool {
        model.isCurrentTrackLoaded && model.queue.current?.track.id == session.track.id
    }

    var body: some View {
        if session.isLoading {
            HStack(spacing: 12) {
                ProgressView().tint(EmberTheme.pink)
                Text("Looking for lyrics…")
                    .font(.system(size: 14))
                    .foregroundStyle(EmberTheme.secondaryText)
            }
            .padding(.vertical, compact ? 24 : 48)
        } else if let errorMessage = session.errorMessage {
            errorState(errorMessage)
        } else if let lyrics = session.lyrics {
            lyricsBody(lyrics)
        } else {
            stateCard(
                icon: "text.quote",
                title: "No lyrics available",
                message: "LRCLIB has no lyrics for this recording."
            )
        }
    }

    @ViewBuilder
    private func lyricsBody(_ lyrics: LyricsResponse) -> some View {
        if lyrics.kind == "instrumental" {
            stateCard(
                icon: "waveform",
                title: "Instrumental",
                message: lyrics.message ?? "This recording has no lyrics."
            )
            attribution(lyrics)
        } else if lyrics.kind == "plain" {
            if !lyrics.lines.isEmpty {
                Text(lyrics.message ?? "These lyrics are not synchronized.")
                    .font(.system(size: 12))
                    .foregroundStyle(EmberTheme.faintText)
                    .padding(.bottom, 4)
                ForEach(lyrics.lines.indices, id: \.self) { index in
                    LyricLineText(
                        line: lyrics.lines[index],
                        active: true,
                        fontSize: lyricSize,
                        time: player.currentTime,
                        offset: session.timingOffset
                    )
                }
                attribution(lyrics)
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
                        seek(to: line, index: index)
                    } label: {
                        LyricLineText(
                            line: line,
                            active: session.activeIndex == index,
                            fontSize: lyricSize,
                            time: player.currentTime,
                            offset: session.timingOffset
                        )
                        .padding(.vertical, compact ? 2 : 4)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSeek)
                    .accessibilityLabel("Seek to \(line.text)")
                    .id(index)
                } else {
                    LyricLineText(
                        line: line,
                        active: false,
                        fontSize: lyricSize,
                        time: player.currentTime,
                        offset: session.timingOffset
                    )
                    .id(index)
                }
            }
            attribution(lyrics)
        } else {
            stateCard(
                icon: "text.quote",
                title: "No lyrics available",
                message: lyrics.message ?? "LRCLIB has no lyrics for this recording."
            )
        }
    }

    private func seek(to line: LyricsLine, index: Int) {
        guard canSeek, let start = line.start else { return }
        let seekTime = LyricsTiming.playbackTime(
            forLyricStart: start,
            offset: session.timingOffset
        )
        player.seek(to: seekTime)
        session.following = true
        session.activeIndex = index
    }

    private func attribution(_ lyrics: LyricsResponse) -> some View {
        HStack(spacing: 7) {
            Image(systemName: "text.quote")
                .font(.system(size: 10))
            Text(lyrics.source == "lrclib" ? "Lyrics from LRCLIB" : "Lyrics")
                .font(.system(size: 11))
            if lyrics.kind == "synced" {
                Text("· timings supplied by the provider")
                    .font(.system(size: 11))
            }
        }
        .foregroundStyle(EmberTheme.faintText)
        .padding(.top, 20)
    }

    private func errorState(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Lyrics unavailable", systemImage: "exclamationmark.triangle.fill")
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(EmberTheme.pinkSoft)
            Text(message)
                .font(.system(size: 13))
                .foregroundStyle(EmberTheme.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
            Button("Try again", action: onRetry)
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(EmberTheme.pink)
        }
        .padding(.vertical, compact ? 22 : 34)
    }

    private func stateCard(icon: String, title: String, message: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icon)
                .font(.system(size: 28, weight: .light))
                .foregroundStyle(EmberTheme.pink.opacity(0.85))
            Text(title)
                .font(.system(.title3, design: .rounded, weight: .bold))
                .foregroundStyle(.white)
            Text(message)
                .font(.system(size: 14))
                .foregroundStyle(EmberTheme.secondaryText)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.vertical, compact ? 20 : 30)
    }
}

private struct LyricLineText: View {
    let line: LyricsLine
    let active: Bool
    let fontSize: CGFloat
    let time: Double
    let offset: Double
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        styledText
            .font(.system(size: fontSize, weight: .semibold, design: .rounded))
            .multilineTextAlignment(.leading)
            .frame(maxWidth: .infinity, alignment: .leading)
            .fixedSize(horizontal: false, vertical: true)
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.24), value: active)
    }

    private var styledText: Text {
        var attributed = AttributedString(line.text)
        attributed.foregroundColor = active ? .white : Color.white.opacity(0.38)
        guard active, !line.words.isEmpty else { return Text(attributed) }

        var searchStart = line.text.startIndex
        for (index, word) in line.words.enumerated() {
            guard !word.text.isEmpty,
                  let found = line.text.range(
                      of: word.text,
                      options: [],
                      range: searchStart..<line.text.endIndex
                  ),
                  let lower = AttributedString.Index(found.lowerBound, within: attributed),
                  let upper = AttributedString.Index(found.upperBound, within: attributed) else {
                continue
            }
            attributed[lower..<upper].foregroundColor = isWordActive(word, at: index, in: line)
                ? EmberTheme.pinkSoft
                : Color.white.opacity(0.9)
            searchStart = found.upperBound
        }
        return Text(attributed)
    }

    private func isWordActive(_ word: LyricsWord, at index: Int, in line: LyricsLine) -> Bool {
        let lyricTime = LyricsTiming.lyricTime(
            forPlaybackTime: time,
            offset: offset
        )
        guard let start = word.start, start <= lyricTime else { return false }
        if let end = word.end {
            return lyricTime < end
        }
        if index + 1 < line.words.count, let next = line.words[index + 1].start {
            return lyricTime < next
        }
        return true
    }
}

private struct ImmersiveLyricsView: View {
    @ObservedObject var session: LyricsSession
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @ScaledMetric(relativeTo: .title) private var lyricSize: CGFloat = 34
    @ScaledMetric(relativeTo: .title2) private var titleSize: CGFloat = 23
    @ScaledMetric(relativeTo: .subheadline) private var artistSize: CGFloat = 13
    let onClose: () -> Void
    let onRetry: () -> Void

    private var synced: Bool {
        session.lyrics?.kind == "synced"
    }

    var body: some View {
        GeometryReader { geometry in
            let compact = geometry.size.height < 520
            let horizontal = geometry.size.width > 700
                ? max(80, (geometry.size.width - 620) / 2)
                : 26
            ZStack {
                LyricsBackdrop(url: session.track.artworkUrl)

                VStack(spacing: 0) {
                    header(compact: compact, horizontal: horizontal)
                    lyricsStage(compact: compact, horizontal: horizontal)
                    LyricsTransportControls(player: player, compact: compact)
                        .padding(.horizontal, horizontal)
                        .padding(.top, compact ? 4 : 10)
                        .padding(.bottom, compact ? 6 : 10)
                }
            }
        }
        .preferredColorScheme(.dark)
        .onAppear {
            session.sync(time: player.currentTime)
        }
        .onChange(of: player.currentTime) { _, time in
            session.sync(time: time)
        }
        .onChange(of: session.track.id) { _, _ in
            session.sync(time: player.currentTime)
        }
    }

    private func header(compact: Bool, horizontal: CGFloat) -> some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                if !compact {
                    Text("Lyrics")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(EmberTheme.pinkSoft.opacity(0.9))
                }
                Text(session.track.title)
                    .font(.system(size: compact ? titleSize * 0.84 : titleSize, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                Text(session.track.artist)
                    .font(.system(size: compact ? artistSize * 0.94 : artistSize, weight: .medium))
                    .foregroundStyle(EmberTheme.secondaryText)
                    .lineLimit(1)
            }
            Spacer(minLength: 10)
            Button(action: onClose) {
                Image(systemName: "chevron.down")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 38, height: 38)
                    .background(Color.white.opacity(0.09))
                    .clipShape(Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Collapse lyrics")
        }
        .padding(.horizontal, horizontal)
        .padding(.top, compact ? 6 : 12)
        .padding(.bottom, compact ? 6 : 10)
        .contentShape(Rectangle())
        .simultaneousGesture(
            DragGesture(minimumDistance: 18)
                .onEnded { value in
                    let vertical = value.translation.height
                    if vertical > 90 && vertical > abs(value.translation.width) {
                        onClose()
                    }
                }
        )
    }

    private func lyricsStage(compact: Bool, horizontal: CGFloat) -> some View {
        ScrollViewReader { proxy in
            ZStack(alignment: .bottom) {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: compact ? 12 : 18) {
                        LyricsContentView(
                            session: session,
                            player: player,
                            lyricSize: min(lyricSize, compact ? 36 : 42),
                            compact: compact
                        ) {
                            onRetry()
                        }
                    }
                    .padding(.horizontal, horizontal)
                    .padding(.top, compact ? 24 : 76)
                    .padding(.bottom, compact ? 72 : 118)
                }
                .scrollIndicators(.hidden)
                .simultaneousGesture(
                    DragGesture(minimumDistance: 8)
                        .onChanged { _ in
                            if synced && session.following {
                                session.following = false
                            }
                        }
                )

                if !session.following && synced {
                    Button {
                        session.following = true
                        scrollLyrics(proxy, to: session.activeIndex, animated: !reduceMotion)
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
                    .padding(.bottom, 10)
                }
            }
            .onAppear {
                if session.following {
                    scrollLyrics(proxy, to: session.activeIndex, animated: false)
                }
            }
            .onChange(of: session.activeIndex) { _, index in
                guard session.following else { return }
                scrollLyrics(proxy, to: index, animated: !reduceMotion)
            }
        }
    }
}

private struct LyricsBackdrop: View {
    let url: URL?

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                EmberTheme.background

                if let url {
                    AsyncImage(url: url) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .scaledToFill()
                        default:
                            Color.clear
                        }
                    }
                    .frame(width: geometry.size.width, height: geometry.size.height)
                    .clipped()
                    .scaleEffect(1.12)
                    .blur(radius: 44)
                    .opacity(0.32)
                }

                LinearGradient(
                    stops: [
                        .init(color: Color.black.opacity(0.22), location: 0),
                        .init(color: EmberTheme.background.opacity(0.86), location: 0.52),
                        .init(color: EmberTheme.background, location: 1)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .ignoresSafeArea()
        .accessibilityHidden(true)
    }
}

private struct LyricsTransportControls: View {
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    var compact = false

    var body: some View {
        HStack(spacing: compact ? 18 : 24) {
            Button {
                model.playPreviousTrack()
            } label: {
                Image(systemName: "backward.fill")
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.82))
                    .frame(width: 40, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Previous track")

            Button {
                player.togglePlayPause()
            } label: {
                ZStack {
                    Circle()
                        .fill(EmberTheme.accentGradient)
                        .frame(width: compact ? 46 : 52, height: compact ? 46 : 52)
                        .shadow(color: EmberTheme.pink.opacity(0.28), radius: 12, y: 4)
                    Image(systemName: model.isPlaybackActive ? "pause.fill" : "play.fill")
                        .font(.system(size: compact ? 18 : 20, weight: .bold))
                        .foregroundStyle(Color.black.opacity(0.8))
                        .offset(x: model.isPlaybackActive ? 0 : 1)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(model.isPlaybackActive ? "Pause" : "Play or retry selected track")

            Button {
                model.playNextTrack()
            } label: {
                Image(systemName: "forward.fill")
                    .font(.system(size: 19, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.82))
                    .frame(width: 40, height: 44)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Next track")

            Spacer(minLength: 8)

            Text("\(Track.format(player.currentTime)) / \(Track.format(player.duration))")
                .font(.system(size: 11, weight: .medium, design: .monospaced))
                .foregroundStyle(EmberTheme.faintText)
                .lineLimit(1)
        }
        .accessibilityElement(children: .contain)
    }
}

private func scrollLyrics(_ proxy: ScrollViewProxy, to index: Int?, animated: Bool) {
    guard let index else { return }
    if animated {
        withAnimation(.easeInOut(duration: 0.42)) {
            proxy.scrollTo(index, anchor: .center)
        }
    } else {
        proxy.scrollTo(index, anchor: .center)
    }
}
