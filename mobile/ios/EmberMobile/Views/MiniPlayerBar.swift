import SwiftUI

struct MiniPlayerBar: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var player: PlayerController

    var body: some View {
        if let track = model.queue.current?.track {
            HStack(spacing: 12) {
                Button {
                    model.activeSheet = .nowPlaying
                } label: {
                    HStack(spacing: 12) {
                        ArtworkView(url: track.artworkUrl, size: 46, corner: 9)
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
                        Spacer(minLength: 6)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                Button {
                    player.togglePlayPause()
                } label: {
                    Image(systemName: model.isPlaybackActive ? "pause.fill" : "play.fill")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(.white)
                        .frame(width: 42, height: 42)
                        .background(EmberTheme.accentGradient)
                        .clipShape(Circle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(model.isPlaybackActive ? "Pause" : "Play or retry selected track")

                Button {
                    model.playNextTrack()
                } label: {
                    Image(systemName: "forward.fill")
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(EmberTheme.secondaryText)
                        .frame(width: 34, height: 42)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Next track")
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(EmberTheme.surface)
            .overlay(alignment: .top) {
                Rectangle()
                    .fill(EmberTheme.pink.opacity(0.25))
                    .frame(height: 1)
            }
        }
    }
}
