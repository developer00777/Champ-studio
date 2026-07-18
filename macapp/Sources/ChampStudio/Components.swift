import SwiftUI
import WebKit

// MARK: - SVG rendering (AsyncImage can't render SVG; use a WKWebView)

struct SVGWebView: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        let webView = WKWebView(frame: .zero, configuration: config)
        webView.setValue(false, forKey: "drawsBackground")
        loadSVG(into: webView)
        return webView
    }

    func updateNSView(_ webView: WKWebView, context: Context) {
        if webView.url != url {
            loadSVG(into: webView)
        }
    }

    private func loadSVG(into webView: WKWebView) {
        // Wrap in a page that centers and letterboxes the SVG.
        let html = """
        <!doctype html><html><head><meta charset="utf-8"><style>
        html,body{margin:0;padding:0;background:transparent;height:100%;overflow:hidden}
        body{display:flex;align-items:center;justify-content:center}
        img{max-width:100%;max-height:100%}
        </style></head><body><img src="\(url.absoluteString)"></body></html>
        """
        webView.loadHTMLString(html, baseURL: url)
    }
}

// MARK: - Asset thumbnail

struct AssetThumb: View {
    let asset: Asset
    @EnvironmentObject private var engine: EngineClient

    var body: some View {
        Group {
            if asset.isSVG {
                SVGWebView(url: engine.resolve(asset.url))
                    .padding(8)
            } else if asset.isVideo {
                ZStack {
                    Theme.bg
                    Image(systemName: "play.circle.fill")
                        .font(.system(size: 34))
                        .foregroundColor(Theme.ember)
                }
            } else if asset.isImage {
                GeometryReader { proxy in
                    AsyncImage(url: engine.resolve(asset.url)) { phase in
                        switch phase {
                        case .success(let image):
                            image
                                .resizable()
                                .aspectRatio(contentMode: .fill)
                                .frame(width: proxy.size.width, height: proxy.size.height)
                                .clipped()
                        case .failure:
                            ZStack {
                                Theme.bg
                                Image(systemName: "exclamationmark.triangle")
                                    .foregroundColor(Theme.secondaryText)
                            }
                            .frame(width: proxy.size.width, height: proxy.size.height)
                        default:
                            ZStack {
                                Theme.bg
                                ProgressView().controlSize(.small)
                            }
                            .frame(width: proxy.size.width, height: proxy.size.height)
                        }
                    }
                }
            } else {
                ZStack {
                    Theme.bg
                    Image(systemName: "doc")
                        .font(.system(size: 28))
                        .foregroundColor(Theme.secondaryText)
                }
            }
        }
    }
}

// MARK: - Asset card (grid cell)

struct AssetCard: View {
    let asset: Asset
    var height: CGFloat = 150

    var body: some View {
        VStack(spacing: 0) {
            AssetThumb(asset: asset)
                .frame(height: height)
                .frame(maxWidth: .infinity)
                .clipped()

            HStack(spacing: 6) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(asset.title)
                        .font(.caption)
                        .foregroundColor(.white)
                        .lineLimit(1)
                    Text(asset.kind.capitalized + (asset.model.map { " · \($0)" } ?? ""))
                        .font(.caption2)
                        .foregroundColor(Theme.secondaryText)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                if asset.isSVG {
                    Text("SVG")
                        .font(.caption2.bold())
                        .padding(.horizontal, 5)
                        .padding(.vertical, 2)
                        .background(Theme.ember.opacity(0.2))
                        .foregroundColor(Theme.ember)
                        .clipShape(Capsule())
                }
            }
            .padding(8)
        }
        .champCard()
    }
}

// MARK: - Section header helper

struct SectionHeader: View {
    let title: String
    var subtitle: String? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.title2.bold())
                .foregroundColor(.white)
            if let subtitle {
                Text(subtitle)
                    .font(.callout)
                    .foregroundColor(Theme.secondaryText)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// MARK: - Library picker sheet (choose an existing asset as a source)

struct LibraryPickerSheet: View {
    /// Restrict to a kind (e.g. "image"); nil shows everything.
    var kind: String? = "image"
    let onPick: (Asset) -> Void

    @EnvironmentObject private var engine: EngineClient
    @Environment(\.dismiss) private var dismiss
    @State private var assets: [Asset] = []
    @State private var loading = true
    @State private var errorMessage: String?

    private let columns = [GridItem(.adaptive(minimum: 160), spacing: 12)]

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Choose from Library")
                    .font(.headline)
                    .foregroundColor(.white)
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding()

            Divider().overlay(Theme.border)

            if loading {
                Spacer()
                ProgressView()
                Spacer()
            } else if assets.isEmpty {
                Spacer()
                Text("No assets yet")
                    .foregroundColor(Theme.secondaryText)
                Spacer()
            } else {
                ScrollView {
                    LazyVGrid(columns: columns, spacing: 12) {
                        ForEach(assets) { asset in
                            AssetCard(asset: asset, height: 110)
                                .onTapGesture {
                                    onPick(asset)
                                    dismiss()
                                }
                        }
                    }
                    .padding()
                }
            }
        }
        .frame(width: 640, height: 480)
        .background(Theme.bg)
        .errorAlert($errorMessage)
        .task {
            do {
                assets = try await engine.assets(kind: kind)
            } catch {
                errorMessage = error.localizedDescription
            }
            loading = false
        }
    }
}
