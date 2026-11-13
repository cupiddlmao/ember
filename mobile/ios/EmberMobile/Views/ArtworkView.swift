import SwiftUI

struct ArtworkView: View {
    let url: URL?
    let size: CGFloat
    var corner: CGFloat = 12

    var body: some View {
        Group {
            if let url {
                AsyncImage(url: ArtworkURL.display(url)) { phase in
                    switch phase {
                    case .success(let image):
                        cover(image)
                    case .failure:
                        if ArtworkURL.display(url) != ArtworkURL.display(url, fallback: true) {
                            AsyncImage(url: ArtworkURL.display(url, fallback: true)) { fallbackPhase in
                                if case .success(let image) = fallbackPhase {
                                    cover(image)
                                } else {
                                    placeholder
                                }
                            }
                        } else {
                            placeholder
                        }
                    default:
                        placeholder
                    }
                }
            } else {
                placeholder
            }
        }
        .frame(width: size, height: size)
        .clipShape(RoundedRectangle(cornerRadius: corner, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: corner, style: .continuous)
                .stroke(Color.white.opacity(0.07), lineWidth: 1)
        )
    }

    private func cover(_ image: Image) -> some View {
        image
            .resizable()
            .scaledToFill()
            .frame(width: size, height: size)
            .clipped()
    }

    private var placeholder: some View {
        ZStack {
            LinearGradient(
                colors: [EmberTheme.surfaceRaised, EmberTheme.surface],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            Image(systemName: "music.note")
                .font(.system(size: size * 0.32, weight: .medium))
                .foregroundStyle(EmberTheme.pink.opacity(0.55))
        }
    }
}
