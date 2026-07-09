// framegrab — extract evenly spaced JPEG frames from a video (CONTRACT §13).
//
// Usage:
//   framegrab <video> <outdir> [maxFrames=12]
//
// Writes frame_00.jpg, frame_01.jpg, ... into <outdir> and prints each written
// path to stdout (one per line). Used by the Slack self-DM media capture path
// when ffmpeg is unavailable.
//
// Compile (build.sh does this; failure there is tolerated with a warning):
//   swiftc -O framegrab.swift -o build/framegrab \
//     -framework AVFoundation -framework CoreImage -framework Foundation

import AVFoundation
import CoreImage
import Foundation

func die(_ msg: String, code: Int32) -> Never {
    FileHandle.standardError.write(("framegrab: " + msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count >= 3 else {
    die("usage: framegrab <video> <outdir> [maxFrames=12]", code: 2)
}

let videoPath = (args[1] as NSString).expandingTildeInPath
let outDir = (args[2] as NSString).expandingTildeInPath
let maxFrames = args.count >= 4 ? max(1, Int(args[3]) ?? 12) : 12

guard FileManager.default.fileExists(atPath: videoPath) else {
    die("video not found: \(videoPath)", code: 1)
}
do {
    try FileManager.default.createDirectory(
        atPath: outDir, withIntermediateDirectories: true)
} catch {
    die("cannot create outdir \(outDir): \(error.localizedDescription)", code: 1)
}

let asset = AVURLAsset(url: URL(fileURLWithPath: videoPath))
let duration = asset.duration
let seconds = CMTimeGetSeconds(duration)
guard seconds.isFinite, seconds > 0 else {
    die("cannot read video duration (unsupported or corrupt file): \(videoPath)", code: 1)
}

let generator = AVAssetImageGenerator(asset: asset)
generator.appliesPreferredTrackTransform = true
// Half-second tolerance: fast seeks, close enough for LLM screenshot analysis.
let tol = CMTime(seconds: 0.5, preferredTimescale: 600)
generator.requestedTimeToleranceBefore = tol
generator.requestedTimeToleranceAfter = tol

let ciContext = CIContext()
let colorSpace = CGColorSpaceCreateDeviceRGB()

var written: [String] = []
for i in 0..<maxFrames {
    // Sample at bucket midpoints — evenly spaced, avoids the (often black)
    // very first frame and the very last frame.
    let t = seconds * (Double(i) + 0.5) / Double(maxFrames)
    let time = CMTime(seconds: t, preferredTimescale: 600)
    let cg: CGImage
    do {
        cg = try generator.copyCGImage(at: time, actualTime: nil)
    } catch {
        FileHandle.standardError.write(
            "framegrab: skip frame \(i) at \(String(format: "%.2f", t))s: \(error.localizedDescription)\n"
                .data(using: .utf8)!)
        continue
    }
    let path = outDir + String(format: "/frame_%02d.jpg", i)
    let url = URL(fileURLWithPath: path)
    do {
        try ciContext.writeJPEGRepresentation(
            of: CIImage(cgImage: cg), to: url, colorSpace: colorSpace,
            options: [:])
    } catch {
        FileHandle.standardError.write(
            "framegrab: write failed \(path): \(error.localizedDescription)\n"
                .data(using: .utf8)!)
        continue
    }
    written.append(path)
    print(path)
}

if written.isEmpty {
    die("no frames extracted from \(videoPath)", code: 1)
}
