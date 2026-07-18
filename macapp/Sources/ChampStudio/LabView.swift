import SwiftUI
import UniformTypeIdentifiers

/// Derivative Lab: run engine transform tools against a source asset.
struct LabView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var appState: AppState

    @State private var tools: [TransformTool] = []
    @State private var selectedToolID: String?
    @State private var paramValues: [String: JSONValue] = [:]
    @State private var source: Asset?
    @State private var results: [Asset] = []
    @State private var isRunning = false
    @State private var isUploading = false
    @State private var showLibraryPicker = false
    @State private var showFileImporter = false
    @State private var errorMessage: String?

    private var selectedTool: TransformTool? {
        tools.first { $0.id == selectedToolID }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(title: "Derivative Lab", subtitle: "Transform assets: upscale, restyle, vectorize and more")
                sourceSection
                toolSection
                resultsSection
            }
            .padding(24)
        }
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .sheet(isPresented: $showLibraryPicker) {
            LibraryPickerSheet(kind: nil) { asset in
                source = asset
            }
        }
        .fileImporter(
            isPresented: $showFileImporter,
            allowedContentTypes: importTypes,
            allowsMultipleSelection: false
        ) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await upload(url) }
            }
        }
        .task {
            await loadTools()
            consumeHandoff()
        }
        .onChange(of: appState.labSource) { _, _ in
            consumeHandoff()
        }
    }

    private var importTypes: [UTType] {
        var types: [UTType] = [.image, .png, .jpeg]
        if let svg = UTType(filenameExtension: "svg") { types.append(svg) }
        return types
    }

    // MARK: Source

    private var sourceSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Source")
                .font(.headline)
                .foregroundColor(.white)

            HStack(spacing: 14) {
                if let source {
                    AssetThumb(asset: source)
                        .frame(width: 140, height: 100)
                        .clipShape(RoundedRectangle(cornerRadius: 8))
                        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))
                    VStack(alignment: .leading, spacing: 4) {
                        Text(source.title)
                            .foregroundColor(.white)
                            .lineLimit(2)
                        Text("\(source.kind) · \(source.mime)")
                            .font(.caption)
                            .foregroundColor(Theme.secondaryText)
                        Button("Clear") { self.source = nil }
                            .buttonStyle(.link)
                    }
                } else {
                    VStack(spacing: 8) {
                        Image(systemName: "square.and.arrow.down.on.square")
                            .font(.system(size: 26))
                            .foregroundColor(Theme.secondaryText)
                        Text("Drop a file here, or choose a source")
                            .font(.callout)
                            .foregroundColor(Theme.secondaryText)
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 100)
                    .background(Theme.bg)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .strokeBorder(Theme.border, style: StrokeStyle(lineWidth: 1, dash: [5]))
                    )
                }
            }
            .dropDestination(for: URL.self) { urls, _ in
                guard let url = urls.first, url.isFileURL else { return false }
                Task { await upload(url) }
                return true
            }

            HStack(spacing: 10) {
                Button {
                    showLibraryPicker = true
                } label: {
                    Label("From Library…", systemImage: "photo.on.rectangle")
                }
                .buttonStyle(.bordered)

                Button {
                    showFileImporter = true
                } label: {
                    if isUploading {
                        HStack(spacing: 6) {
                            ProgressView().controlSize(.small)
                            Text("Uploading…")
                        }
                    } else {
                        Label("Upload File…", systemImage: "square.and.arrow.up")
                    }
                }
                .buttonStyle(.bordered)
                .disabled(isUploading)
            }
        }
        .padding(16)
        .champCard()
    }

    // MARK: Tools

    private var toolSection: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tool")
                .font(.headline)
                .foregroundColor(.white)

            if tools.isEmpty {
                Text("No transform tools available — is the engine running?")
                    .font(.callout)
                    .foregroundColor(Theme.secondaryText)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(tools) { tool in
                            Button {
                                select(tool)
                            } label: {
                                Text(tool.label)
                                    .font(.callout.weight(.medium))
                                    .padding(.horizontal, 12)
                                    .padding(.vertical, 7)
                                    .background(selectedToolID == tool.id ? Theme.ember : Theme.bg)
                                    .foregroundColor(selectedToolID == tool.id ? .white : Theme.secondaryText)
                                    .clipShape(Capsule())
                                    .overlay(Capsule().stroke(Theme.border))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                if let tool = selectedTool {
                    if let description = tool.description, !description.isEmpty {
                        Text(description)
                            .font(.callout)
                            .foregroundColor(Theme.secondaryText)
                    }

                    ForEach(tool.sortedParams, id: \.name) { entry in
                        paramControl(name: entry.name, param: entry.param)
                    }

                    HStack {
                        Spacer()
                        Button {
                            Task { await run(tool) }
                        } label: {
                            if isRunning {
                                HStack(spacing: 8) {
                                    ProgressView().controlSize(.small)
                                    Text("Running…")
                                }
                                .frame(minWidth: 100)
                            } else {
                                Label("Run \(tool.label)", systemImage: "wand.and.stars")
                                    .frame(minWidth: 100)
                            }
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(Theme.ember)
                        .controlSize(.large)
                        .disabled(isRunning || source == nil)
                    }
                }
            }
        }
        .padding(16)
        .champCard()
    }

    @ViewBuilder
    private func paramControl(name: String, param: ToolParam) -> some View {
        let title = name.replacingOccurrences(of: "_", with: " ").capitalized
        switch param.type {
        case "select":
            Picker(title, selection: selectBinding(name, param)) {
                ForEach((param.options ?? []).map(\.display), id: \.self) { option in
                    Text(option).tag(option)
                }
            }
            .frame(maxWidth: 340)
        case "int":
            Stepper(
                "\(title): \(intBinding(name, param).wrappedValue)",
                value: intBinding(name, param),
                in: Int(param.min ?? 0)...Int(param.max ?? 100)
            )
            .fixedSize()
        case "bool":
            Toggle(title, isOn: boolBinding(name, param))
                .toggleStyle(.switch)
                .tint(Theme.ember)
                .fixedSize()
        default:
            HStack(spacing: 8) {
                Text(title)
                    .foregroundColor(Theme.secondaryText)
                TextField("", text: textBinding(name, param))
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 240)
            }
        }
    }

    // MARK: Param bindings

    private func selectBinding(_ name: String, _ param: ToolParam) -> Binding<String> {
        Binding(
            get: {
                paramValues[name]?.display
                    ?? param.def?.display
                    ?? param.options?.first?.display
                    ?? ""
            },
            set: { paramValues[name] = .string($0) }
        )
    }

    private func intBinding(_ name: String, _ param: ToolParam) -> Binding<Int> {
        Binding(
            get: {
                paramValues[name]?.intValue
                    ?? param.def?.intValue
                    ?? Int(param.min ?? 0)
            },
            set: { paramValues[name] = .int($0) }
        )
    }

    private func boolBinding(_ name: String, _ param: ToolParam) -> Binding<Bool> {
        Binding(
            get: { paramValues[name]?.boolValue ?? param.def?.boolValue ?? false },
            set: { paramValues[name] = .bool($0) }
        )
    }

    private func textBinding(_ name: String, _ param: ToolParam) -> Binding<String> {
        Binding(
            get: { paramValues[name]?.display ?? param.def?.display ?? "" },
            set: { text in
                if param.type == "float" || param.type == "number", let d = Double(text) {
                    paramValues[name] = .double(d)
                } else if let i = Int(text), param.type == "int" {
                    paramValues[name] = .int(i)
                } else {
                    paramValues[name] = .string(text)
                }
            }
        )
    }

    // MARK: Results

    private var resultsSection: some View {
        Group {
            if !results.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Text("Results")
                        .font(.headline)
                        .foregroundColor(.white)
                    ScrollView(.horizontal, showsIndicators: true) {
                        HStack(spacing: 12) {
                            ForEach(results) { asset in
                                VStack(spacing: 6) {
                                    AssetCard(asset: asset, height: 120)
                                        .frame(width: 200)
                                        .contextMenu { resultMenu(asset) }
                                    Button("Use as source") {
                                        source = asset
                                    }
                                    .buttonStyle(.bordered)
                                    .controlSize(.small)
                                }
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
                .padding(16)
                .champCard()
            }
        }
    }

    @ViewBuilder
    private func resultMenu(_ asset: Asset) -> some View {
        Button("Use as source") { source = asset }
        Button("Animate") { appState.animate(asset) }
        Button("Save to Downloads") {
            Task {
                do { _ = try await engine.saveToDownloads(asset) }
                catch { errorMessage = error.localizedDescription }
            }
        }
        Divider()
        Button("Delete", role: .destructive) {
            Task {
                do {
                    try await engine.deleteAsset(asset.id)
                    results.removeAll { $0.id == asset.id }
                } catch {
                    errorMessage = error.localizedDescription
                }
            }
        }
    }

    // MARK: Actions

    private func consumeHandoff() {
        if let asset = appState.labSource {
            source = asset
            appState.labSource = nil
        }
    }

    private func loadTools() async {
        do {
            tools = try await engine.transformTools()
            if selectedToolID == nil, let first = tools.first {
                select(first)
            }
        } catch {
            // Engine offline; tools stay empty.
        }
    }

    private func select(_ tool: TransformTool) {
        selectedToolID = tool.id
        var values: [String: JSONValue] = [:]
        for (name, param) in tool.params ?? [:] {
            if let def = param.def, def != .null {
                values[name] = def
            } else if param.type == "select", let first = param.options?.first {
                values[name] = first
            }
        }
        paramValues = values
    }

    private func run(_ tool: TransformTool) async {
        guard let source else { return }
        isRunning = true
        defer { isRunning = false }
        do {
            let assets = try await engine.runTransform(tool: tool.id, assetID: source.id, params: paramValues)
            results.insert(contentsOf: assets, at: 0)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func upload(_ url: URL) async {
        isUploading = true
        defer { isUploading = false }
        do {
            let accessing = url.startAccessingSecurityScopedResource()
            defer { if accessing { url.stopAccessingSecurityScopedResource() } }
            let data = try Data(contentsOf: url)
            let mime = UTType(filenameExtension: url.pathExtension)?.preferredMIMEType
                ?? "application/octet-stream"
            let asset = try await engine.uploadAsset(
                data: data,
                filename: url.lastPathComponent,
                mime: mime
            )
            source = asset
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
