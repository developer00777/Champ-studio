import SwiftUI

/// Image generation.
struct CreateView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var appState: AppState

    @State private var prompt = ""
    @State private var models: [ModelInfo] = []
    @State private var selectedModel = "google/gemini-2.5-flash-image"
    @State private var aspectRatio = "1:1"
    @State private var resolution = "Default"
    @State private var count = 1
    @State private var format = "png"
    @State private var transparent = false
    @State private var isGenerating = false
    @State private var results: [Asset] = []
    @State private var errorMessage: String?
    @State private var savedNote: String?

    private let aspectOptions = ["1:1", "16:9", "9:16", "4:3", "3:4"]
    private let resolutionOptions = ["Default", "1K", "2K", "4K"]
    private let formatOptions = ["png", "jpeg", "webp", "svg"]
    private let columns = [GridItem(.adaptive(minimum: 220), spacing: 14)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(title: "Create", subtitle: "Generate images with the Champ Studio Engine")

                controls

                if !results.isEmpty {
                    Text("Results")
                        .font(.headline)
                        .foregroundColor(.white)
                    LazyVGrid(columns: columns, spacing: 14) {
                        ForEach(results) { asset in
                            AssetCard(asset: asset, height: 180)
                                .contextMenu { resultMenu(asset) }
                        }
                    }
                }
            }
            .padding(24)
        }
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .overlay(alignment: .bottom) {
            if let savedNote {
                Text(savedNote)
                    .font(.caption)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .background(Theme.card)
                    .clipShape(Capsule())
                    .padding(.bottom, 12)
            }
        }
        .task { await loadModels() }
    }

    // MARK: Controls

    private var controls: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Prompt")
                .font(.subheadline)
                .foregroundColor(Theme.secondaryText)
            TextEditor(text: $prompt)
                .font(.body)
                .scrollContentBackground(.hidden)
                .padding(8)
                .frame(minHeight: 84, maxHeight: 140)
                .background(Theme.bg)
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.border))

            HStack(spacing: 16) {
                Picker("Model", selection: $selectedModel) {
                    if !models.contains(where: { $0.id == selectedModel }) {
                        Text(selectedModel).tag(selectedModel)
                    }
                    ForEach(models) { model in
                        Text(model.displayName).tag(model.id)
                    }
                }
                .frame(maxWidth: 320)

                Picker("Resolution", selection: $resolution) {
                    ForEach(resolutionOptions, id: \.self) { Text($0).tag($0) }
                }
                .frame(maxWidth: 200)

                Picker("Format", selection: $format) {
                    ForEach(formatOptions, id: \.self) { Text($0.uppercased()).tag($0) }
                }
                .frame(maxWidth: 180)

                Spacer()
            }

            HStack(spacing: 16) {
                Picker("Aspect", selection: $aspectRatio) {
                    ForEach(aspectOptions, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(maxWidth: 340)

                Stepper("Count: \(count)", value: $count, in: 1...4)
                    .fixedSize()

                Toggle("Transparent", isOn: $transparent)
                    .toggleStyle(.switch)
                    .tint(Theme.ember)
                    .fixedSize()

                Spacer()

                Button {
                    Task { await generate() }
                } label: {
                    if isGenerating {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Generating…")
                        }
                        .frame(minWidth: 110)
                    } else {
                        Label("Generate", systemImage: "sparkles")
                            .frame(minWidth: 110)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.ember)
                .controlSize(.large)
                .disabled(isGenerating || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(16)
        .champCard()
    }

    @ViewBuilder
    private func resultMenu(_ asset: Asset) -> some View {
        Button("Animate") { appState.animate(asset) }
        Button("Open in Lab") { appState.openInLab(asset) }
        Button("Save to Downloads") {
            Task {
                do {
                    let dest = try await engine.saveToDownloads(asset)
                    flash("Saved to \(dest.lastPathComponent)")
                } catch {
                    errorMessage = error.localizedDescription
                }
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

    private func loadModels() async {
        do {
            models = try await engine.imageModels()
            if models.isEmpty {
                models = [ModelInfo(id: "google/gemini-2.5-flash-image", name: "Gemini 2.5 Flash Image", provider: nil, vendor: nil)]
            }
        } catch {
            // Keep the fallback default model; engine may be offline.
            models = [ModelInfo(id: "google/gemini-2.5-flash-image", name: "Gemini 2.5 Flash Image", provider: nil, vendor: nil)]
        }
        if !models.contains(where: { $0.id == selectedModel }), let first = models.first {
            selectedModel = first.id
        }
    }

    private func generate() async {
        isGenerating = true
        defer { isGenerating = false }
        do {
            let request = ImageRequest(
                prompt: prompt,
                model: selectedModel,
                provider: nil,
                n: count,
                aspectRatio: aspectRatio,
                resolution: resolution == "Default" ? nil : resolution,
                outputFormat: format,
                background: transparent ? "transparent" : nil,
                seed: nil,
                inputAssetIDs: []
            )
            let assets = try await engine.generateImages(request)
            results.insert(contentsOf: assets, at: 0)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func flash(_ text: String) {
        savedNote = text
        Task {
            try? await Task.sleep(nanoseconds: 2_500_000_000)
            savedNote = nil
        }
    }
}
