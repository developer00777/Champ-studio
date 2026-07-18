import SwiftUI

/// Root split view: sidebar navigation + section detail.
struct RootView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var launcher: EngineLauncher
    @EnvironmentObject private var appState: AppState

    var body: some View {
        NavigationSplitView {
            AppSidebar()
                .navigationSplitViewColumnWidth(min: 190, ideal: 215, max: 260)
        } detail: {
            detailView
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.bg)
        }
        .background(Theme.bg)
        .task {
            // Auto-start a local engine if needed (bundled engines can take
            // 20-40s to boot; ensureRunning polls health for up to 120s).
            await launcher.ensureRunning(client: engine)
            // Keep the health indicator fresh.
            while !Task.isCancelled {
                _ = await engine.isReachable()
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        switch appState.selection ?? .create {
        case .create: CreateView()
        case .animate: AnimateView()
        case .lab: LabView()
        case .library: LibraryView()
        case .settings: SettingsView()
        }
    }
}

struct AppSidebar: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var launcher: EngineLauncher
    @EnvironmentObject private var appState: AppState
    @State private var starting = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Wordmark(size: 22)
                .padding(.horizontal, 18)
                .padding(.top, 16)
                .padding(.bottom, 8)

            List(selection: $appState.selection) {
                ForEach(SidebarSection.allCases) { section in
                    Label(section.label, systemImage: section.icon)
                        .tag(section)
                        .padding(.vertical, 3)
                }
            }
            .listStyle(.sidebar)
            .scrollContentBackground(.hidden)

            Spacer(minLength: 0)
            engineStatusFooter
        }
        .background(Theme.bg)
    }

    private var engineStatusFooter: some View {
        VStack(alignment: .leading, spacing: 8) {
            Divider().overlay(Theme.border)
            HStack(spacing: 8) {
                if launcher.isStarting {
                    ProgressView()
                        .controlSize(.mini)
                    Text("Starting engine…")
                        .font(.caption)
                        .foregroundColor(Theme.secondaryText)
                } else {
                    Circle()
                        .fill(engine.health != nil ? Color.green : Color.red)
                        .frame(width: 8, height: 8)
                    Text(engine.health != nil ? "Engine online" : "Engine offline")
                        .font(.caption)
                        .foregroundColor(Theme.secondaryText)
                }
                Spacer()
            }
            if let lastError = launcher.lastError, engine.health == nil, !launcher.isStarting {
                Text(lastError)
                    .font(.caption2)
                    .foregroundColor(.red)
                    .lineLimit(3)
            }
            if engine.health == nil && engine.isLocalhost && !launcher.isStarting {
                Button {
                    startEngine()
                } label: {
                    if starting {
                        ProgressView().controlSize(.small)
                    } else {
                        Label("Start Engine", systemImage: "power")
                            .font(.caption)
                    }
                }
                .buttonStyle(.bordered)
                .tint(Theme.ember)
                .disabled(starting)
            }
        }
        .padding(.horizontal, 14)
        .padding(.bottom, 12)
    }

    private func startEngine() {
        starting = true
        launcher.start()
        Task {
            // Frozen (PyInstaller) engines can take 20-40s; poll up to 120s.
            for _ in 0..<60 {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                if await engine.isReachable() { break }
                if !launcher.isRunning { break }
            }
            starting = false
        }
    }
}
