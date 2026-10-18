// swift-tools-version:5.9
// EmoReply iOS 核心包（T01053）：与 m1-poc / android-app 同构的平台无关核心链路。
import PackageDescription

let package = Package(
    name: "EmoReplyCore",
    platforms: [.iOS(.v15), .macOS(.v12)],
    targets: [
        .target(name: "EmoReplyCore", path: "Sources/EmoReplyCore"),
        .testTarget(name: "EmoReplyCoreTests", dependencies: ["EmoReplyCore"], path: "Tests/EmoReplyCoreTests"),
    ]
)
