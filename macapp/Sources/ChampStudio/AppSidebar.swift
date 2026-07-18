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
                Circle()
                    .fill(engine.health != nil ? Color.green : Color.red)
                    .frame(width: 8, height: 8)
                Text(engine.health != nil ? "Engine online" : "Engine offline")
                    .font(.caption)
                    .foregroundColor(Theme.secondaryText)
                Spacer()
            }
            if engine.health == nil && engine.isLocalhost {
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
            // Give the engine a few seconds to boot, polling health.
            for _ in 0..<10 {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                if await engine.isReachable() { break }
            }
            starting = false
        }
    }
}
