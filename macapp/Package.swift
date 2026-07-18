// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "ChampStudio",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "ChampStudio", targets: ["ChampStudio"])
    ],
    targets: [
        .executableTarget(
            name: "ChampStudio",
            path: "Sources/ChampStudio"
        )
    ]
)
