import SwiftUI

struct NowPlayingView: View {
    @ObservedObject var player: PlayerController
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss

    @State private var isScrubbing = false
    @State private var scrubTime: Double = 0

    var body: some View {
        ZStack {
            EmberTheme.backgroundGradient.ignoresSafeArea()

            VStack(spacing: 0) {
                topBar
                Spacer(minLength: 8)

                if let track = player.track {
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
    }

    private var topBar: some View {
        HStack {
            Text("NOW PLAYING")
                .font(.system(size: 11, weight: .bold))
                .tracking(2.2)
                .foregroundStyle(EmberTheme.faintText)
            Spacer()
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
        HStack(spacing: 42) {
            Button {
                player.seek(to: max(0, player.currentTime - 15))
            } label: {
                Image(systemName: "gobackward.15")
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(.white.opacity(0.85))
            }
            .buttonStyle(.plain)

            Button {
                player.togglePlayPause()
            } label: {
                ZStack {
                    Circle()
                        .fill(EmberTheme.accentGradient)
                        .frame(width: 76, height: 76)
                        .shadow(color: EmberTheme.pink.opacity(0.35), radius: 18, y: 6)
                    Image(systemName: player.state == .playing ? "pause.fill" : "play.fill")
                        .font(.system(size: 28, weight: .bold))
                        .foregroundStyle(Color.black.opacity(0.8))
                        .offset(x: player.state == .playing ? 0 : 2)
                }
            }
            .buttonStyle(.plain)

            Button {
                player.seek(to: min(player.duration, player.currentTime + 15))
            } label: {
                Image(systemName: "goforward.15")
                    .font(.system(size: 26, weight: .medium))
                    .foregroundStyle(.white.opacity(0.85))
            }
            .buttonStyle(.plain)
        }
        .padding(.top, 12)
    }

    private var footer: some View {
        HStack(spacing: 7) {
            Image(systemName: "lock.fill")
                .font(.system(size: 9))
            Text("Private playback from your Ember server")
                .font(.system(size: 11))
        }
        .foregroundStyle(EmberTheme.faintText)
        .padding(.bottom, 12)
    }
}
