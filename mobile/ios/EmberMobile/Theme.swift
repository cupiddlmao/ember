import SwiftUI

enum EmberTheme {
    static let background = Color(red: 0.035, green: 0.027, blue: 0.035)
    static let surface = Color(red: 0.094, green: 0.071, blue: 0.086)
    static let surfaceRaised = Color(red: 0.137, green: 0.098, blue: 0.122)
    static let pink = Color(red: 1.0, green: 0.31, blue: 0.55)
    static let pinkDeep = Color(red: 0.78, green: 0.10, blue: 0.34)
    static let pinkSoft = Color(red: 1.0, green: 0.62, blue: 0.76)
    static let secondaryText = Color.white.opacity(0.68)
    static let faintText = Color.white.opacity(0.50)
    static let hairline = Color.white.opacity(0.06)
    /// Tint for the selected / currently playing row, mirroring desktop.
    static let selectedFill = pink.opacity(0.12)
    static let selectedStroke = pink.opacity(0.28)
    static let pressedFill = Color.white.opacity(0.06)

    static let cardRadius: CGFloat = 16
    static let controlRadius: CGFloat = 12
    static let press = Animation.easeOut(duration: 0.14)
    static let stateChange = Animation.easeInOut(duration: 0.22)

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
            .clipShape(RoundedRectangle(cornerRadius: EmberTheme.cardRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: EmberTheme.cardRadius, style: .continuous)
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
            .clipShape(RoundedRectangle(cornerRadius: EmberTheme.controlRadius, style: .continuous))
            .opacity(configuration.isPressed ? 0.85 : 1)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(EmberTheme.press, value: configuration.isPressed)
    }
}

/// Plain-looking rows that still acknowledge a tap: a faint fill and a
/// slight press-in, released quickly.
struct EmberRowPressStyle: ButtonStyle {
    var radius: CGFloat = EmberTheme.controlRadius

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .background(
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(configuration.isPressed ? EmberTheme.pressedFill : Color.clear)
            )
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .animation(EmberTheme.press, value: configuration.isPressed)
    }
}
