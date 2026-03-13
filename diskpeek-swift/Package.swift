// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "diskpeek",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "diskpeek",
            path: "Sources/diskpeek"
        )
    ]
)
