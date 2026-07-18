import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var launcher: EngineLauncher

    @AppStorage("engineURL") private var engineURL = EngineClient.defaultBaseURL
    @AppStorage("engineToken") private var engineToken = ""
    @AppStorage("enginePath") private var enginePath = EngineLauncher.defaultEnginePath

    @State private var testResult: String?
    @State private var testOK = false
    @State private var testing = false

    @State private var openrouterKey = ""
    @State private var muapiKey = ""
    @State private var loadedOpenrouter = ""
    @State private var loadedMuapi = ""
    @State private var savingKeys = false
    @State private var keysNote: String?
    @State private var errorMessage: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(title: "Settings", subtitle: "Engine connection, local process and API keys")

                connectionSection
                processSection
                keysSection
            }
            .padding(24)
        }
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .onAppear {
            openrouterKey = Keychain.get("openrouter") ?? ""
            muapiKey = Keychain.get("muapi") ?? ""
            loadedOpenrouter = openrouterKey
            loadedMuapi = muapiKey
        }
        .task { _ = await engine.isReachable() }
    }

    // MARK: Connection

    private var connectionSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Engine Connection")
                .font(.headline)
                .foregroundColor(.white)

            HStack(spacing: 10) {
                TextField("http://127.0.0.1:8787", text: $engineURL)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 380)

                Button {
                    Task { await testConnection() }
                } label: {
                    if testing {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("Test Connection")
                    }
                }
                .buttonStyle(.bordered)
                .disabled(testing)

                if let testResult {
                    Label(testResult, systemImage: testOK ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .font(.caption)
                        .foregroundColor(testOK ? .green : .red)
                }
                Spacer()
            }

            Text("Point at a shared engine to collaborate with teammates. Assets and jobs live on the engine.")
                .font(.caption)
                .foregroundColor(Theme.secondaryText)

            HStack(spacing: 10) {
                Text("Access token (optional)")
                    .font(.callout)
                    .foregroundColor(Theme.secondaryText)
                SecureField("Bearer token", text: $engineToken)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 300)
            }
        }
        .padding(16)
        .champCard()
    }

    // MARK: Local process

    private var processSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Local Engine")
                .font(.headline)
                .foregroundColor(.white)

            HStack(spacing: 10) {
                Text("Engine path")
                    .font(.callout)
                    .foregroundColor(Theme.secondaryText)
                TextField(EngineLauncher.defaultEnginePath, text: $enginePath)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 420)
            }

            HStack(spacing: 10) {
                if launcher.isRunning {
                    Button {
                        launcher.stop()
                    } label: {
                        Label("Stop Engine", systemImage: "stop.fill")
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                } else {
                    Button {
                        launcher.start(enginePath: enginePath)
                        Task {
                            for _ in 0..<10 {
                                try? await Task.sleep(nanoseconds: 1_000_000_000)
                                if await engine.isReachable() { break }
                            }
                        }
                    } label: {
                        Label("Start Engine", systemImage: "power")
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.ember)
                }

                HStack(spacing: 6) {
                    Circle()
                        .fill(launcher.isRunning ? Color.green : Theme.secondaryText)
                        .frame(width: 8, height: 8)
                    Text(launcher.isRunning ? "Process running" : "Not running")
                        .font(.caption)
                        .foregroundColor(Theme.secondaryText)
                }

                if let lastError = launcher.lastError {
                    Text(lastError)
                        .font(.caption)
                        .foregroundColor(.red)
                }
                Spacer()
            }

            Text("Runs .venv/bin/python -m champstudio.main inside the engine folder. The process is terminated when the app quits.")
                .font(.caption)
                .foregroundColor(Theme.secondaryText)
        }
        .padding(16)
        .champCard()
    }

    // MARK: API keys

    private var keysSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("API Keys")
                .font(.headline)
                .foregroundColor(.white)

            keyRow(label: "OpenRouter", keyName: "openrouter", text: $openrouterKey)
            keyRow(label: "MuAPI", keyName: "muapi", text: $muapiKey)

            HStack(spacing: 12) {
                Button {
                    Task { await saveKeys() }
                } label: {
                    if savingKeys {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Saving…")
                        }
                    } else {
                        Label("Save Keys", systemImage: "key.fill")
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.ember)
                .disabled(savingKeys)

                if let keysNote {
                    Text(keysNote)
                        .font(.caption)
                        .foregroundColor(.green)
                }
                Spacer()
            }

            Text("Keys are stored in the macOS Keychain and pushed to the engine. Clearing a field removes the key.")
                .font(.caption)
                .foregroundColor(Theme.secondaryText)
        }
        .padding(16)
        .champCard()
    }

    private func keyRow(label: String, keyName: String, text: Binding<String>) -> some View {
        HStack(spacing: 10) {
            Text(label)
                .font(.callout)
                .foregroundColor(Theme.secondaryText)
                .frame(width: 90, alignment: .leading)
            SecureField("\(label) API key", text: text)
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 380)
            if let health = engine.health {
                Label(
                    health.hasKey(keyName) ? "Configured on engine" : "Missing on engine",
                    systemImage: health.hasKey(keyName) ? "checkmark.circle.fill" : "exclamationmark.circle"
                )
                .font(.caption)
                .foregroundColor(health.hasKey(keyName) ? .green : Theme.secondaryText)
            }
            Spacer()
        }
    }

    // MARK: Actions

    private func testConnection() async {
        testing = true
        defer { testing = false }
        do {
            let health = try await engine.fetchHealth()
            testOK = true
            let version = health.version.map { " v\($0)" } ?? ""
            testResult = "Connected: \(health.name ?? "engine")\(version)"
        } catch {
            testOK = false
            testResult = error.localizedDescription
        }
    }

    private func saveKeys() async {
        savingKeys = true
        defer { savingKeys = false }
        keysNote = nil

        // 1. Keychain (empty value deletes the item).
        Keychain.set(openrouterKey, for: "openrouter")
        Keychain.set(muapiKey, for: "muapi")

        // 2. Push only changed keys to the engine ("" clears).
        var changed: [String: String] = [:]
        if openrouterKey != loadedOpenrouter { changed["openrouter"] = openrouterKey }
        if muapiKey != loadedMuapi { changed["muapi"] = muapiKey }

        if !changed.isEmpty {
            do {
                try await engine.updateKeys(changed)
                loadedOpenrouter = openrouterKey
                loadedMuapi = muapiKey
                keysNote = "Saved to Keychain and engine"
            } catch {
                keysNote = nil
                errorMessage = "Saved to Keychain, but the engine update failed: \(error.localizedDescription)"
            }
        } else {
            keysNote = "Saved to Keychain (no changes to push)"
        }

        _ = try? await engine.fetchHealth()
    }
}
