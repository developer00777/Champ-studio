import SwiftUI
import AVKit

/// Video generation with job polling.
struct AnimateView: View {
    @EnvironmentObject private var engine: EngineClient
    @EnvironmentObject private var appState: AppState

    @State private var prompt = ""
    @State private var models: [ModelInfo] = []
    @State private var selectedModel = "google/veo-3.1"
    @State private var durationText = "8"
    @State private var aspectRatio = "16:9"
    @State private var generateAudio = true
    @State private var sourceImage: Asset?
    @State private var showLibraryPicker = false
    @State private var jobs: [VideoJob] = []
    @State private var pollers: [String: Task<Void, Never>] = [:]
    @State private var isSubmitting = false
    @State private var playJob: VideoJob?
    @State private var errorMessage: String?

    private let aspectOptions = ["16:9", "9:16", "1:1"]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                SectionHeader(title: "Animate", subtitle: "Generate videos, optionally from a library image")
                controls
                jobsList
            }
            .padding(24)
        }
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .sheet(isPresented: $showLibraryPicker) {
            LibraryPickerSheet(kind: "image") { asset in
                sourceImage = asset
            }
        }
        .sheet(item: $playJob) { job in
            VideoPlayerSheet(job: job)
        }
        .task {
            await loadModels()
            await refreshJobs()
            consumeHandoff()
        }
        .onChange(of: appState.animateSource) { _, _ in
            consumeHandoff()
        }
        .onDisappear {
            for task in pollers.values { task.cancel() }
            pollers = [:]
        }
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
                .frame(minHeight: 72, maxHeight: 130)
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
                .frame(maxWidth: 300)

                HStack(spacing: 6) {
                    Text("Duration")
                        .foregroundColor(Theme.secondaryText)
                    TextField("8", text: $durationText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 52)
                    Text("s")
                        .foregroundColor(Theme.secondaryText)
                }

                aspectChips

                Toggle("Audio", isOn: $generateAudio)
                    .toggleStyle(.switch)
                    .tint(Theme.ember)
                    .fixedSize()

                Spacer()
            }

            HStack(spacing: 12) {
                if let sourceImage {
                    HStack(spacing: 8) {
                        AssetThumb(asset: sourceImage)
                            .frame(width: 54, height: 40)
                            .clipShape(RoundedRectangle(cornerRadius: 6))
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Source image")
                                .font(.caption)
                                .foregroundColor(Theme.secondaryText)
                            Text(sourceImage.title)
                                .font(.caption)
                                .foregroundColor(.white)
                                .lineLimit(1)
                                .frame(maxWidth: 220, alignment: .leading)
                        }
                        Button {
                            self.sourceImage = nil
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundColor(Theme.secondaryText)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(6)
                    .background(Theme.bg)
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                } else {
                    Button {
                        showLibraryPicker = true
                    } label: {
                        Label("Source image…", systemImage: "photo.badge.plus")
                    }
                    .buttonStyle(.bordered)
                }

                Spacer()

                Button {
                    Task { await submit() }
                } label: {
                    if isSubmitting {
                        HStack(spacing: 8) {
                            ProgressView().controlSize(.small)
                            Text("Submitting…")
                        }
                        .frame(minWidth: 110)
                    } else {
                        Label("Animate", systemImage: "film")
                            .frame(minWidth: 110)
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(Theme.ember)
                .controlSize(.large)
                .disabled(isSubmitting || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(16)
        .champCard()
    }

    private var aspectChips: some View {
        HStack(spacing: 6) {
            ForEach(aspectOptions, id: \.self) { option in
                Button {
                    aspectRatio = option
                } label: {
                    Text(option)
                        .font(.caption.bold())
                        .padding(.horizontal, 10)
                        .padding(.vertical, 5)
                        .background(aspectRatio == option ? Theme.ember : Theme.bg)
                        .foregroundColor(aspectRatio == option ? .white : Theme.secondaryText)
                        .clipShape(Capsule())
                        .overlay(Capsule().stroke(Theme.border))
                }
                .buttonStyle(.plain)
            }
        }
    }

    // MARK: Jobs

    private var jobsList: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Jobs")
                    .font(.headline)
                    .foregroundColor(.white)
                Spacer()
                Button {
                    Task { await refreshJobs() }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
            }

            if jobs.isEmpty {
                Text("No video jobs yet")
                    .font(.callout)
                    .foregroundColor(Theme.secondaryText)
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 30)
            } else {
                VStack(spacing: 8) {
                    ForEach(jobs) { job in
                        jobRow(job)
                    }
                }
            }
        }
        .padding(16)
        .champCard()
    }

    private func jobRow(_ job: VideoJob) -> some View {
        HStack(spacing: 12) {
            statusIcon(job.status)
                .frame(width: 22)

            VStack(alignment: .leading, spacing: 2) {
                Text(job.prompt ?? job.id)
                    .font(.callout)
                    .foregroundColor(.white)
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(job.status.replacingOccurrences(of: "_", with: " "))
                        .font(.caption)
                        .foregroundColor(job.status == "failed" ? .red : Theme.secondaryText)
                    if let model = job.model {
                        Text("· \(model)")
                            .font(.caption)
                            .foregroundColor(Theme.secondaryText)
                    }
                }
                if job.status == "failed", let error = job.error {
                    Text(error)
                        .font(.caption)
                        .foregroundColor(.red)
                        .lineLimit(2)
                }
            }

            Spacer()

            if job.status == "completed", job.asset != nil {
                Button {
                    playJob = job
                } label: {
                    Label("Play", systemImage: "play.fill")
                }
                .buttonStyle(.bordered)
                .tint(Theme.ember)
            }
        }
        .padding(10)
        .background(Theme.bg)
        .clipShape(RoundedRectangle(cornerRadius: 8))
    }

    @ViewBuilder
    private func statusIcon(_ status: String) -> some View {
        switch status {
        case "completed":
            Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
        case "failed":
            Image(systemName: "xmark.circle.fill").foregroundColor(.red)
        case "in_progress":
            ProgressView().controlSize(.small)
        default:
            Image(systemName: "clock").foregroundColor(Theme.secondaryText)
        }
    }

    // MARK: Actions

    private func consumeHandoff() {
        if let asset = appState.animateSource {
            sourceImage = asset
            appState.animateSource = nil
        }
    }

    private func loadModels() async {
        do {
            models = try await engine.videoModels()
        } catch {
            models = []
        }
        if models.isEmpty {
            models = [ModelInfo(id: "google/veo-3.1", name: "Veo 3.1", provider: nil, vendor: nil)]
        }
        if !models.contains(where: { $0.id == selectedModel }), let first = models.first {
            selectedModel = first.id
        }
    }

    private func refreshJobs() async {
        do {
            jobs = try await engine.videoJobs()
                .sorted { ($0.createdAt ?? 0) > ($1.createdAt ?? 0) }
            for job in jobs where job.isActive {
                poll(job.id)
            }
        } catch {
            // Engine may be offline; leave the list as-is.
        }
    }

    private func submit() async {
        isSubmitting = true
        defer { isSubmitting = false }
        do {
            let request = VideoRequest(
                prompt: prompt,
                model: selectedModel,
                duration: Int(durationText.trimmingCharacters(in: .whitespaces)),
                aspectRatio: aspectRatio,
                resolution: nil,
                generateAudio: generateAudio,
                imageAssetID: sourceImage?.id
            )
            let resp = try await engine.createVideo(request)
            await refreshJobs()
            poll(resp.jobID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Poll a single job every 3 seconds until it reaches a terminal state.
    private func poll(_ id: String) {
        guard pollers[id] == nil else { return }
        pollers[id] = Task {
            defer { pollers[id] = nil }
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 3_000_000_000)
                guard let job = try? await engine.videoJob(id) else { continue }
                upsert(job)
                if !job.isActive { break }
            }
        }
    }

    private func upsert(_ job: VideoJob) {
        if let idx = jobs.firstIndex(where: { $0.id == job.id }) {
            jobs[idx] = job
        } else {
            jobs.insert(job, at: 0)
        }
    }
}

// MARK: - Player sheet

struct VideoPlayerSheet: View {
    let job: VideoJob
    @EnvironmentObject private var engine: EngineClient
    @Environment(\.dismiss) private var dismiss
    @State private var player: AVPlayer?

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(job.prompt ?? "Video")
                    .font(.headline)
                    .foregroundColor(.white)
                    .lineLimit(1)
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding()

            if let player {
                VideoPlayer(player: player)
                    .frame(minWidth: 700, minHeight: 420)
            } else {
                Spacer()
                Text("Video unavailable")
                    .foregroundColor(Theme.secondaryText)
                Spacer()
            }
        }
        .frame(width: 740, height: 500)
        .background(Theme.bg)
        .onAppear {
            if let asset = job.asset {
                let p = AVPlayer(url: engine.resolve(asset.url))
                player = p
                p.play()
            }
        }
        .onDisappear {
            player?.pause()
        }
    }
}
