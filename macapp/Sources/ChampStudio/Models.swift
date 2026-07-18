import Foundation

// MARK: - JSONValue (heterogeneous JSON for transform tool params)

enum JSONValue: Codable, Hashable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)
    case array([JSONValue])
    case object([String: JSONValue])
    case null

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            self = .null
        } else if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let i = try? c.decode(Int.self) {
            self = .int(i)
        } else if let d = try? c.decode(Double.self) {
            self = .double(d)
        } else if let s = try? c.decode(String.self) {
            self = .string(s)
        } else if let a = try? c.decode([JSONValue].self) {
            self = .array(a)
        } else if let o = try? c.decode([String: JSONValue].self) {
            self = .object(o)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .int(let i): try c.encode(i)
        case .double(let d): try c.encode(d)
        case .bool(let b): try c.encode(b)
        case .array(let a): try c.encode(a)
        case .object(let o): try c.encode(o)
        case .null: try c.encodeNil()
        }
    }

    var stringValue: String? {
        if case .string(let s) = self { return s }
        return nil
    }

    var intValue: Int? {
        switch self {
        case .int(let i): return i
        case .double(let d): return Int(d)
        case .string(let s): return Int(s)
        default: return nil
        }
    }

    var doubleValue: Double? {
        switch self {
        case .double(let d): return d
        case .int(let i): return Double(i)
        case .string(let s): return Double(s)
        default: return nil
        }
    }

    var boolValue: Bool? {
        if case .bool(let b) = self { return b }
        return nil
    }

    /// Human-readable rendering, used for pickers and text fields.
    var display: String {
        switch self {
        case .string(let s): return s
        case .int(let i): return String(i)
        case .double(let d): return d == d.rounded() ? String(Int(d)) : String(d)
        case .bool(let b): return b ? "true" : "false"
        case .array(let a): return a.map(\.display).joined(separator: ", ")
        case .object: return "{…}"
        case .null: return ""
        }
    }
}

// MARK: - Core entities

struct Asset: Codable, Identifiable, Hashable {
    let id: String
    let kind: String
    let mime: String
    let filename: String
    let prompt: String?
    let model: String?
    let provider: String?
    let tool: String?
    let parentID: String?
    let cost: Double?
    let createdAt: String
    let url: String

    enum CodingKeys: String, CodingKey {
        case id, kind, mime, filename, prompt, model, provider, tool, cost, url
        case parentID = "parent_id"
        case createdAt = "created_at"
    }

    var isImage: Bool { mime.hasPrefix("image/") && mime != "image/svg+xml" }
    var isSVG: Bool { mime == "image/svg+xml" }
    var isVideo: Bool { mime.hasPrefix("video/") || kind == "video" }

    var title: String {
        if let prompt, !prompt.isEmpty { return prompt }
        if !filename.isEmpty { return filename }
        return id
    }
}

struct Health: Codable {
    let status: String
    let name: String?
    let version: String?
    let keys: [String: Bool]?

    func hasKey(_ name: String) -> Bool { keys?[name] ?? false }
}

struct ModelInfo: Codable, Identifiable, Hashable {
    let id: String
    let name: String?
    let provider: String?
    let vendor: String?

    var displayName: String { name ?? id }
}

struct VideoJob: Codable, Identifiable, Hashable {
    let id: String
    let status: String
    let model: String?
    let prompt: String?
    let asset: Asset?
    let error: String?
    let createdAt: Double?

    enum CodingKeys: String, CodingKey {
        case id, status, model, prompt, asset, error
        case createdAt = "created_at"
    }

    var isActive: Bool { status == "pending" || status == "in_progress" }
}

struct TransformTool: Codable, Identifiable, Hashable {
    let id: String
    let label: String
    let description: String?
    let params: [String: ToolParam]?

    var sortedParams: [(name: String, param: ToolParam)] {
        (params ?? [:]).sorted { $0.key < $1.key }.map { (name: $0.key, param: $0.value) }
    }
}

struct ToolParam: Codable, Hashable {
    let type: String
    let options: [JSONValue]?
    let min: Double?
    let max: Double?
    let def: JSONValue?

    enum CodingKeys: String, CodingKey {
        case type, options, min, max
        case def = "default"
    }
}

// MARK: - Request bodies

struct ImageRequest: Encodable {
    var prompt: String
    var model: String
    var provider: String?
    var n: Int
    var aspectRatio: String?
    var resolution: String?
    var outputFormat: String?
    var background: String?
    var seed: Int?
    var inputAssetIDs: [String] = []

    enum CodingKeys: String, CodingKey {
        case prompt, model, provider, n, resolution, background, seed
        case aspectRatio = "aspect_ratio"
        case outputFormat = "output_format"
        case inputAssetIDs = "input_asset_ids"
    }
}

struct VideoRequest: Encodable {
    var prompt: String
    var model: String
    var duration: Int?
    var aspectRatio: String?
    var resolution: String?
    var generateAudio: Bool?
    var imageAssetID: String?

    enum CodingKeys: String, CodingKey {
        case prompt, model, duration, resolution
        case aspectRatio = "aspect_ratio"
        case generateAudio = "generate_audio"
        case imageAssetID = "image_asset_id"
    }
}

// MARK: - Response wrappers

struct AssetsResponse: Codable {
    let assets: [Asset]
}

struct SingleAssetResponse: Codable {
    let asset: Asset
}

struct JobsResponse: Codable {
    let jobs: [VideoJob]
}

struct VideoCreateResponse: Codable {
    let jobID: String
    let status: String

    enum CodingKeys: String, CodingKey {
        case jobID = "job_id"
        case status
    }
}

struct ToolsResponse: Codable {
    let tools: [TransformTool]
}

struct SettingsResponse: Codable {
    let keys: [String: String?]?
}

struct ErrorDetail: Codable {
    let detail: JSONValue
}
