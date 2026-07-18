import SwiftUI
import AppKit

// MARK: - Sidebar sections

enum SidebarSection: String, CaseIterable, Identifiable {
    case create
    case animate
    case lab
    case library
    case settings

    var id: String { rawValue }

    var label: String {
        switch self {
        case .create: return "Create"
        case .animate: return "Animate"
        case .lab: return "Derivative Lab"
        case .library: return "Library"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .create: return "sparkles"
        case .animate: return "film"
        case .lab: return "wand.and.stars"
        case .library: return "photo.on.rectangle.angled"
        case .settings: return "gearshape"
        }
    }
}

// MARK: - Shared app state (cross-view handoffs)

@MainActor
final class AppState: ObservableObject {
    @Published var selection: SidebarSection? = .create
    /// Set when "Animate" is chosen on an image; consumed by AnimateView.
    @Published var animateSource: Asset?
    /// Set when "Open in Lab" is chosen; consumed by LabView.
    @Published var labSource: Asset?

    func animate(_ asset: Asset) {
        animateSource = asset
        selection = .animate
    }

    func openInLab(_ asset: Asset) {
        labSource = asset
        selection = .lab
    }
}

// MARK: - App delegate (terminate the local engine on quit)

final class AppDelegate: NSObject, NSApplicationDelegate {
    static weak var launcher: EngineLauncher?

    func applicationWillTerminate(_ notification: Notification) {
        if Thread.isMainThread {
            MainActor.assumeIsolated {
                AppDelegate.launcher?.stop()
            }
        }
    }
}

// MARK: - Entry point

@main
struct ChampStudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var engine = EngineClient()
    @StateObject private var launcher = EngineLauncher()
    @StateObject private var appState = AppState()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(engine)
                .environmentObject(launcher)
                .environmentObject(appState)
                .frame(minWidth: 1080, minHeight: 680)
                .preferredColorScheme(.dark)
                .onAppear { AppDelegate.launcher = launcher }
        }
    }
}
