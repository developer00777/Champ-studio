import SwiftUI

extension Color {
    init(hex: UInt32, opacity: Double = 1.0) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255.0,
            green: Double((hex >> 8) & 0xFF) / 255.0,
            blue: Double(hex & 0xFF) / 255.0,
            opacity: opacity
        )
    }
}

enum Theme {
    /// App background
    static let bg = Color(hex: 0x0B0D12)
    /// Card / panel surface
    static let card = Color(hex: 0x1E222D)
    /// Borders and separators
    static let border = Color(hex: 0x2A2F3D)
    /// Champ ember accent
    static let ember = Color(hex: 0xFF6B35)
    /// Secondary text
    static let secondaryText = Color(hex: 0x6B7280)
}

/// "Champ" white bold + "Studio" ember wordmark.
struct Wordmark: View {
    var size: CGFloat = 20

    var body: some View {
        HStack(spacing: 4) {
            Text("Champ")
                .font(.system(size: size, weight: .bold, design: .rounded))
                .foregroundColor(.white)
            Text("Studio")
                .font(.system(size: size, weight: .bold, design: .rounded))
                .foregroundColor(Theme.ember)
        }
    }
}

extension View {
    /// Champ card chrome: dark surface, rounded corners, subtle border.
    func champCard(cornerRadius: CGFloat = 12) -> some View {
        self
            .background(Theme.card)
            .clipShape(RoundedRectangle(cornerRadius: cornerRadius, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .stroke(Theme.border, lineWidth: 1)
            )
    }

    /// Standard "Error" alert bound to an optional message.
    func errorAlert(_ message: Binding<String?>) -> some View {
        alert(
            "Error",
            isPresented: Binding(
                get: { message.wrappedValue != nil },
                set: { if !$0 { message.wrappedValue = nil } }
            )
        ) {
            Button("OK", role: .cancel) { message.wrappedValue = nil }
        } message: {
            Text(message.wrappedValue ?? "Unknown error")
        }
    }
}
