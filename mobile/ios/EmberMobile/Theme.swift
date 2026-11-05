import SwiftUI

enum EmberTheme {
    static let background = Color(red: 0.035, green: 0.027, blue: 0.035)
    static let surface = Color(red: 0.094, green: 0.071, blue: 0.086)
    static let surfaceRaised = Color(red: 0.137, green: 0.098, blue: 0.122)
    static let pink = Color(red: 1.0, green: 0.31, blue: 0.55)
    static let pinkDeep = Color(red: 0.78, green: 0.10, blue: 0.34)
    static let pinkSoft = Color(red: 1.0, green: 0.62, blue: 0.76)
    static let secondaryText = Color.white.opacity(0.58)
    static let faintText = Color.white.opacity(0.38)
    static let hairline = Color.white.opacity(0.08)

    static var backgroundGradient: LinearGradient {
        LinearGradient(
            colors: [
                Color(red: 0.12, green: 0.035, blue: 0.075),
                background,
                background
            ],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }

    static var accentGradient: LinearGradient {
        LinearGradient(
            colors: [pinkSoft, pink, pinkDeep],
            startPoint: .topLeading,
            endPoint: .bottomTrailing
        )
    }
}

private struct EmberCard: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(EmberTheme.surface)
            .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: 18, style: .continuous)
                    .stroke(EmberTheme.hairline, lineWidth: 1)
            )
    }
}

extension View {
    func emberCard() -> some View {
        modifier(EmberCard())
    }
}

struct PrimaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 16, weight: .semibold, design: .rounded))
            .foregroundStyle(Color.black.opacity(0.82))
            .frame(maxWidth: .infinity)
            .padding(.vertical, 15)
            .background(EmberTheme.accentGradient)
            .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
            .opacity(configuration.isPressed ? 0.8 : 1)
    }
}
