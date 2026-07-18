import SwiftUI
import AVKit

/// Asset library with kind filter, detail sheet and lineage.
struct LibraryView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var appState: AppState

    @State private var kindFilter = "all"
    @State private var assets: [Asset] = []
    @State private var loading = false
    @State private var selectedAsset: Asset?
    @State private var errorMessage: String?

    private let kinds = ["all", "image", "video", "vector"]
    private let columns = [GridItem(.adaptive(minimum: 210), spacing: 14)]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                SectionHeader(title: "Library", subtitle: "Everything generated or uploaded")
                Spacer()
                Picker("", selection: $kindFilter) {
                    ForEach(kinds, id: \.self) { kind in
                        Text(kind.capitalized).tag(kind)
                    }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 320)

                Button {
                    Task { await refresh() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
            }
            .padding(.horizontal, 24)
            .padding(.top, 24)

            if loading && assets.isEmpty {
                Spacer()
                HStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
                Spacer()
            } else if assets.isEmpty {
                Spacer()
                VStack(spacing: 8) {
                    Image(systemName: "photo.stack")
                        .font(.system(size: 34))
                        .foregroundColor(Theme.secondaryText)
                    Text("No assets yet — generate something in Create")
                        .foregroundColor(Theme.secondaryText)
                }
                .frame(maxWidth: .infinity)
                Spacer()
            } else {
                ScrollView {
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(assets) { asset in
                            AssetCard(asset: asset, height: 160)
                                .onTapGesture { selectedAsset = asset }
                                .contextMenu { assetMenu(asset) }
                        }
                    }
                    .padding(.horizontal, 24)
                    .padding(.bottom, 24)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .sheet(item: $selectedAsset) { asset in
            AssetDetailSheet(assetID: asset.id) {
                Task { await refresh() }
            }
        }
        .task(id: kindFilter) { await refresh() }
    }

    @ViewBuilder
    private func assetMenu(_ asset: Asset) -> some View {
        if asset.isImage || asset.isSVG {
            Button("Animate") { appState.animate(asset) }
        }
        Button("Open in Lab") { appState.openInLab(asset) }
        Button("Save to Downloads") {
            Task {
                do { _ = try await engine.saveToDownloads(asset) }
                catch { errorMessage = error.localizedDescription }
            }
        }
        Divider()
        Button("Delete", role: .destructive) {
            Task { await delete(asset) }
        }
    }

    private func refresh() async {
        loading = true
        defer { loading = false }
        do {
            assets = try await engine.assets(kind: kindFilter == "all" ? nil : kindFilter)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func delete(_ asset: Asset) async {
        do {
            try await engine.deleteAsset(asset.id)
            assets.removeAll { $0.id == asset.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

// MARK: - Detail sheet with lineage

struct AssetDetailSheet: View {
    let assetID: String
    var onChange: () -> Void = {}

    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var appState: AppState
    @Environment(\.dismiss) private var dismiss

    @State private var asset: Asset?
    @State private var lineage: [Asset] = []
    @State private var errorMessage: String?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(asset?.title ?? "Asset")
                    .font(.headline)
                    .foregroundColor(.white)
                    .lineLimit(1)
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding()

            Divider().overlay(Theme.border)

            if let asset {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        preview(asset)
                            .frame(maxWidth: .infinity)
                            .frame(height: 320)
                            .background(Color.black.opacity(0.3))
                            .clipShape(RoundedRectangle(cornerRadius: 10))

                        metadata(asset)

                        if !lineage.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("Lineage")
                                    .font(.headline)
                                    .foregroundColor(.white)
                                ScrollView(.horizontal, showsIndicators: false) {
                                    HStack(spacing: 10) {
                                        ForEach(lineage) { ancestor in
                                            AssetCard(asset: ancestor, height: 90)
                                                .frame(width: 150)
                                        }
                                    }
                                }
                            }
                        }

                        HStack(spacing: 10) {
                            if asset.isImage || asset.isSVG {
                                Button("Animate") {
                                    dismiss()
                                    appState.animate(asset)
                                }
                                .buttonStyle(.bordered)
                            }
                            Button("Open in Lab") {
                                dismiss()
                                appState.openInLab(asset)
                            }
                            .buttonStyle(.bordered)
                            Button("Save to Downloads") {
                                Task {
                                    do { _ = try await engine.saveToDownloads(asset) }
                                    catch { errorMessage = error.localizedDescription }
                                }
                            }
                            .buttonStyle(.bordered)
                            Spacer()
                            Button(role: .destructive) {
                                Task {
                                    do {
                                        try await engine.deleteAsset(asset.id)
                                        onChange()
                                        dismiss()
                                    } catch {
                                        errorMessage = error.localizedDescription
                                    }
                                }
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                    .padding()
                }
            } else {
                Spacer()
                ProgressView()
                Spacer()
            }
        }
        .frame(width: 720, height: 620)
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .task {
            do {
                let detail = try await engine.assetDetail(assetID)
                asset = detail.asset
                lineage = detail.lineage
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    @ViewBuilder
    private func preview(_ asset: Asset) -> some View {
        if asset.isVideo {
            VideoPlayer(player: AVPlayer(url: engine.resolve(asset.url)))
        } else if asset.isSVG {
            SVGWebView(url: engine.resolve(asset.url))
                .padding(12)
        } else if asset.isImage {
            AsyncImage(url: engine.resolve(asset.url)) { phase in
                switch phase {
                case .success(let image):
                    image.resizable().aspectRatio(contentMode: .fit)
                case .failure:
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundColor(Theme.secondaryText)
                default:
                    ProgressView()
                }
            }
        } else {
            Image(systemName: "doc")
                .font(.system(size: 40))
                .foregroundColor(Theme.secondaryText)
        }
    }

    private func metadata(_ asset: Asset) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            metaRow("Kind", asset.kind)
            metaRow("MIME", asset.mime)
            metaRow("File", asset.filename)
            if let model = asset.model { metaRow("Model", model) }
            if let provider = asset.provider { metaRow("Provider", provider) }
            if let tool = asset.tool { metaRow("Tool", tool) }
            if let prompt = asset.prompt, !prompt.isEmpty { metaRow("Prompt", prompt) }
            if let cost = asset.cost { metaRow("Cost", String(format: "$%.4f", cost)) }
            metaRow("Created", asset.createdAt)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.card)
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }

    private func metaRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Text(label)
                .font(.caption.bold())
                .foregroundColor(Theme.secondaryText)
                .frame(width: 70, alignment: .leading)
            Text(value)
                .font(.caption)
                .foregroundColor(.white)
                .textSelection(.enabled)
        }
    }
}
