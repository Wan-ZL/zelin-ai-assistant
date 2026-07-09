import AppKit

// Renders the Zelin's AI Assistant app icon at all iconset sizes.
// Design: indigo→violet squircle + bold white checkmark (approval) + AI sparkle.

let outDir = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "."

func star4(center: NSPoint, outer: CGFloat, inner: CGFloat) -> NSBezierPath {
    let p = NSBezierPath()
    // 4 outer points (up, right, down, left) with concave inner points between
    let pts: [(CGFloat, CGFloat)] = [
        (0, outer), (inner, inner), (outer, 0), (inner, -inner),
        (0, -outer), (-inner, -inner), (-outer, 0), (-inner, inner),
    ]
    for (i, d) in pts.enumerated() {
        let pt = NSPoint(x: center.x + d.0, y: center.y + d.1)
        if i == 0 { p.move(to: pt) } else { p.line(to: pt) }
    }
    p.close()
    return p
}

func renderIcon(size: Int) -> Data? {
    let S = CGFloat(size)
    guard let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0
    ) else { return nil }
    rep.size = NSSize(width: S, height: S)

    guard let ctx = NSGraphicsContext(bitmapImageRep: rep) else { return nil }
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = ctx
    let cg = ctx.cgContext
    cg.setShouldAntialias(true)
    cg.interpolationQuality = .high

    // content region (transparent margin like Apple template ~9%)
    let m = S * 0.09
    let C = S - 2 * m
    let rect = NSRect(x: m, y: m, width: C, height: C)
    let corner = C * 0.2237
    let squircle = NSBezierPath(roundedRect: rect, xRadius: corner, yRadius: corner)

    // background gradient: light indigo-violet (top-left) -> deep violet (bottom-right)
    let top = NSColor(calibratedRed: 0.494, green: 0.435, blue: 0.965, alpha: 1) // #7E6FF6
    let bot = NSColor(calibratedRed: 0.357, green: 0.129, blue: 0.714, alpha: 1) // #5B21B6
    if let grad = NSGradient(colors: [top, bot]) {
        grad.draw(in: squircle, angle: -45)
    }

    // soft top gloss
    squircle.setClip()
    if let gloss = NSGradient(colors: [
        NSColor(white: 1, alpha: 0.22), NSColor(white: 1, alpha: 0.0),
    ]) {
        let glossRect = NSRect(x: m, y: m + C * 0.42, width: C, height: C * 0.58)
        gloss.draw(in: glossRect, angle: 90)
    }
    NSGraphicsContext.current?.cgContext.resetClip()
    // re-clip for subsequent draws staying inside squircle
    squircle.setClip()

    func cx(_ f: CGFloat) -> CGFloat { m + f * C }  // content x
    func cy(_ f: CGFloat) -> CGFloat { m + f * C }  // content y (y-up)

    // subtle drop shadow for the glyph
    let sh = NSShadow()
    sh.shadowColor = NSColor(white: 0, alpha: 0.28)
    sh.shadowOffset = NSSize(width: 0, height: -C * 0.015)
    sh.shadowBlurRadius = C * 0.03
    sh.set()

    // checkmark (y-up): left arm -> bottom vertex -> right arm
    let check = NSBezierPath()
    check.move(to: NSPoint(x: cx(0.28), y: cy(0.52)))
    check.line(to: NSPoint(x: cx(0.44), y: cy(0.35)))
    check.line(to: NSPoint(x: cx(0.75), y: cy(0.68)))
    check.lineWidth = C * 0.115
    check.lineCapStyle = .round
    check.lineJoinStyle = .round
    NSColor.white.setStroke()
    check.stroke()

    // AI sparkle near top-right of the check
    let sp = star4(center: NSPoint(x: cx(0.77), y: cy(0.75)),
                   outer: C * 0.10, inner: C * 0.032)
    NSColor(white: 1, alpha: 0.96).setFill()
    sp.fill()

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])
}

let sizes = [16, 32, 64, 128, 256, 512, 1024]
for s in sizes {
    if let data = renderIcon(size: s) {
        let path = "\(outDir)/icon_\(s).png"
        try? data.write(to: URL(fileURLWithPath: path))
        print("wrote \(path)")
    } else {
        print("FAILED size \(s)")
    }
}
