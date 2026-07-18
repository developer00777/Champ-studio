import Foundation

// MARK: - Errors

enum EngineError: LocalizedError {
    case badURL
    case badResponse
    case network(String)
    case server(Int, String)
    case decoding(String)

    var errorDescription: String? {
        switch self {
        case .badURL:
            return "Invalid engine URL — check Settings."
        case .badResponse:
            return "Unexpected response from the engine."
        case .network(let msg):
            return "Cannot reach the engine: \(msg)"
        case .server(let code, let msg):
            return "Engine error (\(code)): \(msg)"
        case .decoding(let msg):
            return "Failed to decode engine response: \(msg)"
        }
    }
}

// MARK: - Client

/// Async/await wrapper around the Champ Studio Engine REST API.
@MainActor
final class EngineClient: ObservableObject {
    static let defaultBaseURL = "http://127.0.0.1:8787"

    @Published var health: Health?

    private let session: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 320   // image generation is synchronous, allow 300s+
        cfg.timeoutIntervalForResource = 640
        return URLSession(configuration: cfg)
    }()

    var baseURL: URL {
        let raw = (UserDefaults.standard.string(forKey: "engineURL") ?? Self.defaultBaseURL)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return URL(string: raw.isEmpty ? Self.defaultBaseURL : raw)
            ?? URL(string: Self.defaultBaseURL)!
    }

    var isLocalhost: Bool {
        let host = baseURL.host ?? ""
        return host == "127.0.0.1" || host == "localhost" || host == "::1"
    }

    /// Resolve an engine-relative asset URL against the base URL.
    func resolve(_ path: String) -> URL {
        if path.hasPrefix("http://") || path.hasPrefix("https://") {
            return URL(string: path) ?? baseURL
        }
        return URL(string: path, relativeTo: baseURL)?.absoluteURL ?? baseURL
    }

    // MARK: Plumbing

    private func urlRequest(
        _ method: String,
        _ path: String,
        body: Data? = nil,
        contentType: String? = "application/json",
        timeout: TimeInterval = 60
    ) throws -> URLRequest {
        guard let url = URL(string: path, relativeTo: baseURL)?.absoluteURL else {
            throw EngineError.badURL
        }
        var req = URLRequest(url: url, timeoutInterval: timeout)
        req.httpMethod = method
        if let body {
            req.httpBody = body
            if let contentType {
                req.setValue(contentType, forHTTPHeaderField: "Content-Type")
            }
        }
        let token = UserDefaults.standard.string(forKey: "engineToken") ?? ""
        if !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        return req
    }

    private func runData(_ req: URLRequest) async throws -> Data {
        let data: Data
        let resp: URLResponse
        do {
            (data, resp) = try await session.data(for: req)
        } catch {
            throw EngineError.network(error.localizedDescription)
        }
        guard let http = resp as? HTTPURLResponse else { throw EngineError.badResponse }
        guard (200..<300).contains(http.statusCode) else {
            let message: String
            if let err = try? JSONDecoder().decode(ErrorDetail.self, from: data) {
                message = err.detail.display
            } else {
                message = String(data: data, encoding: .utf8) ?? "HTTP \(http.statusCode)"
            }
            throw EngineError.server(http.statusCode, message)
        }
        return data
    }

    private func decodeT<T: Decodable>(_ data: Data) throws -> T {
        do {
            return try JSONDecoder().decode(T.self, from: data)
        } catch {
            throw EngineError.decoding(String(describing: error))
        }
    }

    private func get<T: Decodable>(_ path: String, timeout: TimeInterval = 60) async throws -> T {
        let req = try urlRequest("GET", path, timeout: timeout)
        return try decodeT(try await runData(req))
    }

    private func send<B: Encodable, T: Decodable>(
        _ method: String,
        _ path: String,
        _ body: B,
        timeout: TimeInterval = 60
    ) async throws -> T {
        let data = try JSONEncoder().encode(body)
        let req = try urlRequest(method, path, body: data, timeout: timeout)
        return try decodeT(try await runData(req))
    }

    // MARK: Health & settings

    @discardableResult
    func fetchHealth(timeout: TimeInterval = 5) async throws -> Health {
        let req = try urlRequest("GET", "/api/health", timeout: timeout)
        let h: Health = try decodeT(try await runData(req))
        health = h
        return h
    }

    func isReachable() async -> Bool {
        (try? await fetchHealth()) != nil
    }

    func getSettings() async throws -> SettingsResponse {
        try await get("/api/settings")
    }

    /// Push changed keys to the engine ("" clears a key).
    func updateKeys(_ keys: [String: String]) async throws {
        struct Body: Encodable { let keys: [String: String] }
        let data = try JSONEncoder().encode(Body(keys: keys))
        let req = try urlRequest("PUT", "/api/settings", body: data)
        _ = try await runData(req)
    }

    // MARK: Models

    func imageModels() async throws -> [ModelInfo] {
        try await get("/api/models/images")
    }

    func videoModels() async throws -> [ModelInfo] {
        try await get("/api/models/videos")
    }

    // MARK: Images

    func generateImages(_ body: ImageRequest) async throws -> [Asset] {
        let resp: AssetsResponse = try await send("POST", "/api/images", body, timeout: 300)
        return resp.assets
    }

    // MARK: Videos

    func createVideo(_ body: VideoRequest) async throws -> VideoCreateResponse {
        try await send("POST", "/api/videos", body)
    }

    func videoJobs() async throws -> [VideoJob] {
        let resp: JobsResponse = try await get("/api/videos/jobs")
        return resp.jobs
    }

    func videoJob(_ id: String) async throws -> VideoJob {
        try await get("/api/videos/jobs/\(id)")
    }

    // MARK: Transform (Derivative Lab)

    func transformTools() async throws -> [TransformTool] {
        let resp: ToolsResponse = try await get("/api/transform")
        return resp.tools
    }

    func runTransform(tool: String, assetID: String, params: [String: JSONValue]) async throws -> [Asset] {
        struct Body: Encodable {
            let assetID: String
            let params: [String: JSONValue]
            enum CodingKeys: String, CodingKey {
                case assetID = "asset_id"
                case params
            }
        }
        let resp: AssetsResponse = try await send(
            "POST", "/api/transform/\(tool)",
            Body(assetID: assetID, params: params),
            timeout: 300
        )
        return resp.assets
    }

    // MARK: Assets

    func assets(kind: String? = nil) async throws -> [Asset] {
        var path = "/api/assets"
        if let kind, !kind.isEmpty {
            path += "?kind=\(kind.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? kind)"
        }
        let resp: AssetsResponse = try await get(path)
        return resp.assets
    }

    func assetDetail(_ id: String) async throws -> (asset: Asset, lineage: [Asset]) {
        let req = try urlRequest("GET", "/api/assets/\(id)")
        let data = try await runData(req)
        let decoder = JSONDecoder()

        struct LineageWrapper: Decodable { let lineage: [Asset]? }
        let lineage = (try? decoder.decode(LineageWrapper.self, from: data))?.lineage ?? []

        if let asset = try? decoder.decode(Asset.self, from: data) {
            return (asset, lineage)
        }
        if let wrapped = try? decoder.decode(SingleAssetResponse.self, from: data) {
            return (wrapped.asset, lineage)
        }
        throw EngineError.decoding("Unrecognized asset detail payload")
    }

    func deleteAsset(_ id: String) async throws {
        let req = try urlRequest("DELETE", "/api/assets/\(id)")
        _ = try await runData(req)
    }

    /// Multipart upload; the engine expects the file under form field "file".
    func uploadAsset(data fileData: Data, filename: String, mime: String) async throws -> Asset {
        let boundary = "champ-\(UUID().uuidString)"
        var body = Data()
        body.append(Data("--\(boundary)\r\n".utf8))
        body.append(Data("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8))
        body.append(Data("Content-Type: \(mime)\r\n\r\n".utf8))
        body.append(fileData)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))

        let req = try urlRequest(
            "POST", "/api/assets/upload",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)",
            timeout: 120
        )
        let data = try await runData(req)
        let decoder = JSONDecoder()
        if let asset = try? decoder.decode(Asset.self, from: data) {
            return asset
        }
        if let wrapped = try? decoder.decode(SingleAssetResponse.self, from: data) {
            return wrapped.asset
        }
        if let listed = try? decoder.decode(AssetsResponse.self, from: data), let first = listed.assets.first {
            return first
        }
        throw EngineError.decoding("Unrecognized upload response")
    }

    // MARK: Convenience

    /// Download an asset's bytes and save them into ~/Downloads.
    func saveToDownloads(_ asset: Asset) async throws -> URL {
        let (data, _) = try await session.data(from: resolve(asset.url))
        let dir = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask).first
            ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Downloads")
        let name = asset.filename.isEmpty ? "\(asset.id).bin" : asset.filename
        var dest = dir.appendingPathComponent(name)
        // Avoid clobbering: add a numeric suffix if needed.
        var counter = 1
        let ext = dest.pathExtension
        let base = dest.deletingPathExtension().lastPathComponent
        while FileManager.default.fileExists(atPath: dest.path) {
            let suffixed = ext.isEmpty ? "\(base)-\(counter)" : "\(base)-\(counter).\(ext)"
            dest = dir.appendingPathComponent(suffixed)
            counter += 1
        }
        try data.write(to: dest)
        return dest
    }
}
