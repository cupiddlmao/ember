import SwiftUI

struct NowPlayingView: View {
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var isScrubbing = false
    @State private var scrubTime: Double = 0
    @State private var showingQueue = false
    @State private var showingLyrics = false

    var body: some View {
        ZStack {
            EmberTheme.backgroundGradient.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                Spacer(minLength: 8)

                if let track = model.queue.current?.track {
                    artwork(track)
                    details(track)
                    progress
                    controls
                } else {
                    VStack(spacing: 12) {
                        Image(systemName: "music.note")
                            .font(.system(size: 38, weight: .light))
                            .foregroundStyle(EmberTheme.faintText)
                        Text("Nothing playing")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundStyle(EmberTheme.secondaryText)
                    }
                }

                Spacer(minLength: 10)
                footer
            }
            .padding(.horizontal, 26)
        }
        .presentationDetents([.large])
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showingQueue) {
            QueueView()
                .environmentObject(model)
        }
        .fullScreenCover(isPresented: $showingLyrics) {
            if let track = model.queue.current?.track {
                LyricsView(track: track, player: player)
                    .environmentObject(model)
                    .id(track.id)
            }
        }
    }

    private var topBar: some View {
        HStack {
            Text("NOW PLAYING")
                .font(.system(size: 11, weight: .bold))
                .tracking(2.2)
                .foregroundStyle(EmberTheme.faintText)
            Spacer()
            HStack(spacing: 8) {
                Button {
                    showingLyrics = true
                } label: {
                    Image(systemName: "quote.bubble.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Lyrics")
                .disabled(!model.isCurrentTrackLoaded)

                Button {
                    showingQueue = true
                } label: {
                    Image(systemName: "list.bullet")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Queue")

                Button {
                    dismiss()
                } label: {
                    Image(systemName: "chevron.down")
                        .font(.system(size: 14, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 34, height: 34)
                        .background(Color.white.opacity(0.08))
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Close player")
            }
        }
        .padding(.top, 18)
    }

    private func artwork(_ track: Track) -> some View {
        ArtworkView(url: track.artworkUrl, size: 300, corner: 26)
            .shadow(color: EmberTheme.pink.opacity(0.28), radius: 34, y: 16)
            .padding(.top, 18)
    }

    private func details(_ track: Track) -> some View {
        VStack(spacing: 6) {
            Text(track.title)
                .font(.system(size: 23, weight: .bold, design: .rounded))
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)
                .lineLimit(2)
            Text(track.artist)
                .font(.system(size: 15, weight: .medium))
                .foregroundStyle(EmberTheme.pinkSoft)
                .lineLimit(1)
            if !track.album.isEmpty {
                Text(track.album)
                    .font(.system(size: 13))
                    .foregroundStyle(EmberTheme.faintText)
                    .lineLimit(1)
            }
            if let error = player.errorMessage {
                Text(error)
                    .font(.system(size: 12))
                    .foregroundStyle(EmberTheme.pinkSoft)
                    .padding(.top, 4)
            }
        }
        .padding(.top, 22)
    }

    private var progress: some View {
        VStack(spacing: 4) {
            Slider(
                value: Binding(
                    get: { isScrubbing ? scrubTime : player.currentTime },
                    set: { scrubTime = $0 }
                ),
                in: 0...max(player.duration, 0.01),
                onEditingChanged: { editing in
                    if editing {
                        scrubTime = player.currentTime
                        isScrubbing = true
                    } else {
                        player.seek(to: scrubTime)
                        isScrubbing = false
                    }
                }
            )
            .tint(EmberTheme.pink)
            .disabled(!model.isCurrentTrackLoaded)

            HStack {
                Text(Track.format(isScrubbing ? scrubTime : player.currentTime))
                Spacer()
                Text(Track.format(player.duration))
            }
            .font(.system(size: 11, weight: .medium, design: .monospaced))
            .foregroundStyle(EmberTheme.faintText)
        }
        .padding(.top, 20)
    }

    private var controls: some View {
        HStack(spacing: 22) {
            Button {
                model.playPreviousTrack()
            } label: {
                Image(systemName: "backward.fill")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                    .frame(width: 40, height: 48)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Previous track")

            Button {
                player.seek(to: max(0, player.currentTime - 15))
            } label: {
                Image(systemName: "gobackward.15")
                    .font(.system(size: 21, weight: .medium))
                    .foregroundStyle(.white.opacity(0.7))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Back 15 seconds")
            .disabled(!model.isCurrentTrackLoaded)

            Button {
                player.togglePlayPause()
            } label: {
                ZStack {
                    Circle()
                        .fill(EmberTheme.accentGradient)
                        .frame(width: 76, height: 76)
                        .shadow(color: EmberTheme.pink.opacity(0.35), radius: 18, y: 6)
                    Image(systemName: model.isPlaybackActive ? "pause.fill" : "play.fill")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(Color.black.opacity(0.8))
                        .offset(x: model.isPlaybackActive ? 0 : 2)
                }
            }
            .buttonStyle(.plain)
            .accessibilityLabel(model.isPlaybackActive ? "Pause" : "Play or retry selected track")

            Button {
                player.seek(to: min(player.duration, player.currentTime + 15))
            } label: {
                Image(systemName: "goforward.15")
                    .font(.system(size: 21, weight: .medium))
                    .foregroundStyle(.white.opacity(0.7))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Forward 15 seconds")
            .disabled(!model.isCurrentTrackLoaded)

            Button {
                model.playNextTrack()
            } label: {
                Image(systemName: "forward.fill")
                    .font(.system(size: 22, weight: .semibold))
                    .foregroundStyle(.white.opacity(0.85))
                    .frame(width: 40, height: 48)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Next track")
        }
        .padding(.top, 10)
    }

    private var footer: some View {
        HStack(spacing: 7) {
            Image(systemName: "lock.fill")
                .font(.system(size: 9))
            Text("Private playback · \(model.queue.upcoming.count) queued")
                .font(.system(size: 11))
        }
        .foregroundStyle(EmberTheme.faintText)
        .padding(.bottom, 12)
    }
}
