import Foundation

/// Starts/stops a local Champ Studio Engine process.
///
/// Preference order:
/// 1. A bundled PyInstaller engine at Contents/Resources/engine/champstudio-engine
///    (self-contained distribution; launched directly, no shell involved).
/// 2. The developer repo checkout, run via zsh:
///    `cd '<enginePath>' && exec .venv/bin/python -m champstudio.main`.
///    That path may contain spaces and '&' (default:
///    ~/Apps&Projects/Champ Studio/engine), so it is always single-quoted.
@MainActor
final class EngineLauncher: ObservableObject {
    static let defaultEnginePath = "~/Apps&Projects/Champ Studio/engine"
    static let defaultPort = 8787

    @Published private(set) var process: Process?
    @Published var lastError: String?
    /// True while the auto-starter is waiting for the engine to become healthy
    /// (a frozen PyInstaller engine can take 20-40s to boot).
    @Published private(set) var isStarting = false

    var isRunning: Bool { process?.isRunning ?? false }

    private var didAttemptAutoStart = false

    // MARK: Paths

    /// The bundled engine executable, if this app was built with --embed-engine.
    static var bundledEngineExecutable: URL? {
        guard let resources = Bundle.main.resourceURL else { return nil }
        let url = resources.appendingPathComponent("engine/champstudio-engine")
        return FileManager.default.isExecutableFile(atPath: url.path) ? url : nil
    }

    static var configuredEnginePath: String {
        let stored = UserDefaults.standard.string(forKey: "enginePath") ?? ""
        return stored.isEmpty ? defaultEnginePath : stored
    }

    /// Single-quote a string for safe interpolation into a zsh command line.
    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Port taken from the configured engine URL (Settings).
    private static var configuredPort: Int? {
        let raw = UserDefaults.standard.string(forKey: "engineURL") ?? ""
        guard !raw.isEmpty, let components = URLComponents(string: raw) else { return nil }
        return components.port
    }

    // MARK: Lifecycle

    func start(enginePath: String? = nil) {
        guard !isRunning else { return }
        lastError = nil

        let p: Process
        if let bundled = Self.bundledEngineExecutable {
            // Self-contained engine: exec the frozen binary directly.
            p = Process()
            p.executableURL = bundled
            p.currentDirectoryURL = bundled.deletingLastPathComponent()
            var env = ProcessInfo.processInfo.environment
            if let port = Self.configuredPort, port != Self.defaultPort {
                env["CHAMP_PORT"] = String(port)
            }
            p.environment = env
        } else {
            // Developer fallback: repo checkout with a Python venv.
            let raw = enginePath ?? Self.configuredEnginePath
            let expanded = (raw as NSString).expandingTildeInPath
            guard FileManager.default.fileExists(atPath: expanded) else {
                lastError = "No bundled engine, and engine folder not found at \(expanded)"
                return
            }
            p = Process()
            p.executableURL = URL(fileURLWithPath: "/bin/zsh")
            p.arguments = [
                "-c",
                "cd \(Self.shellQuote(expanded)) && exec .venv/bin/python -m champstudio.main"
            ]
        }

        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        p.terminationHandler = { [weak self] proc in
            Task { @MainActor in
                guard let self else { return }
                if self.process === proc {
                    self.process = nil
                    if proc.terminationStatus != 0 && proc.terminationReason == .exit {
                        self.lastError = "Engine exited with status \(proc.terminationStatus)"
                    }
                }
            }
        }

        do {
            try p.run()
            process = p
        } catch {
            process = nil
            lastError = "Could not start engine: \(error.localizedDescription)"
        }
    }

    /// Auto-start for app launch: if the configured engine URL is localhost and
    /// unreachable, start a local engine and poll /api/health every 2s for up
    /// to 120s (frozen engines start slowly). Designers never press a button.
    func ensureRunning(client: EngineClient) async {
        guard !didAttemptAutoStart else { return }
        didAttemptAutoStart = true

        guard client.isLocalhost else { return }
        if await client.isReachable() { return }
        guard !isRunning else { return }

        isStarting = true
        defer { isStarting = false }

        start()
        guard process != nil else { return }  // launch failed; lastError is set

        let deadline = Date().addingTimeInterval(120)
        while Date() < deadline {
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if await client.isReachable() { return }
            if !isRunning {
                if lastError == nil { lastError = "Engine process exited during startup" }
                return
            }
        }
        lastError = "Engine did not become healthy within 120s"
    }

    /// Terminate the engine process (also called on app quit).
    func stop() {
        guard let p = process else { return }
        process = nil
        p.terminationHandler = nil
        if p.isRunning {
            p.terminate()
        }
    }
}
