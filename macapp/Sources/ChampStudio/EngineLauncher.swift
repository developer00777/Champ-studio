import Foundation

/// Starts/stops a local Champ Studio Engine process.
///
/// The engine path may contain spaces and '&' (default:
/// ~/Apps&Projects/Champ Studio/engine), so it is always single-quoted
/// before being handed to the shell.
@MainActor
final class EngineLauncher: ObservableObject {
    static let defaultEnginePath = "~/Apps&Projects/Champ Studio/engine"

    @Published private(set) var process: Process?
    @Published var lastError: String?

    var isRunning: Bool { process?.isRunning ?? false }

    static var configuredEnginePath: String {
        let stored = UserDefaults.standard.string(forKey: "enginePath") ?? ""
        return stored.isEmpty ? defaultEnginePath : stored
    }

    /// Single-quote a string for safe interpolation into a zsh command line.
    static func shellQuote(_ s: String) -> String {
        "'" + s.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    func start(enginePath: String? = nil) {
        guard !isRunning else { return }
        lastError = nil

        let raw = enginePath ?? Self.configuredEnginePath
        let expanded = (raw as NSString).expandingTildeInPath

        guard FileManager.default.fileExists(atPath: expanded) else {
            lastError = "Engine folder not found at \(expanded)"
            return
        }

        let command = "cd \(Self.shellQuote(expanded)) && exec .venv/bin/python -m champstudio.main"

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/zsh")
        p.arguments = ["-c", command]
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
