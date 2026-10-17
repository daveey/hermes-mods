// hermes-vdisplay: create an invisible virtual display that lives while this process runs.
//
// Prints one line `display <id> bounds <x>,<y> <w>x<h> main <x>,<y> <w>x<h>` at start and again whenever
// its own or the main display's global bounds change (monitors plugged/unplugged re-arrange the desktop). The display is parked
// diagonally off the bottom-right corner of the arrangement so the cursor practically never wanders in.
//
// Build: swiftc -O -import-objc-header CGVirtualDisplay.h vdisplay.swift -o hermes-vdisplay
// Size:  HERMES_VDISPLAY_SIZE=1440x900 (default)
import Foundation
import CoreGraphics

var w: UInt32 = 1440, h: UInt32 = 900
if let s = ProcessInfo.processInfo.environment["HERMES_VDISPLAY_SIZE"] {
    let p = s.split(separator: "x").compactMap { UInt32($0) }
    if p.count == 2 { w = p[0]; h = p[1] }
}
let d = CGVirtualDisplayDescriptor()
d.queue = DispatchQueue.main
d.name = "Hermes Agent Display"
d.maxPixelsWide = w; d.maxPixelsHigh = h
d.sizeInMillimeters = CGSize(width: 600, height: 375)
d.vendorID = 0x4845; d.productID = 0x524d; d.serialNum = 1
d.terminationHandler = { _, _ in exit(0) }
guard let vd = CGVirtualDisplay(descriptor: d) else { print("create failed"); exit(1) }
let s = CGVirtualDisplaySettings()
s.hiDPI = 0
s.modes = [CGVirtualDisplayMode(width: w, height: h, refreshRate: 30)]
guard vd.apply(s) else { print("applySettings failed"); exit(1) }
let id = vd.displayID

func otherDisplays() -> [CGDirectDisplayID] {
    var n: UInt32 = 0
    CGGetActiveDisplayList(0, nil, &n)
    var ids = [CGDirectDisplayID](repeating: 0, count: Int(n))
    CGGetActiveDisplayList(n, &ids, &n)
    return ids.filter { $0 != id }
}

// Park at the bottom-right corner of the union of the real displays (corner contact only).
func park() {
    var maxX: CGFloat = 0, maxY: CGFloat = 0
    for o in otherDisplays() {
        let b = CGDisplayBounds(o)
        maxX = max(maxX, b.maxX); maxY = max(maxY, b.maxY)
    }
    var cfg: CGDisplayConfigRef?
    CGBeginDisplayConfiguration(&cfg)
    CGConfigureDisplayOrigin(cfg, id, Int32(maxX), Int32(maxY))
    CGCompleteDisplayConfiguration(cfg, .forSession)
}

func overlapsOthers(_ b: CGRect) -> Bool {
    // Edge-adjacent (not just corner) means the cursor can slide in along a whole side.
    for o in otherDisplays() {
        let ob = CGDisplayBounds(o)
        if b.insetBy(dx: -1, dy: -1).intersection(ob).width > 2 && b.insetBy(dx: -1, dy: -1).intersection(ob).height > 2 { return true }
    }
    return false
}

var last = CGRect.null, lastMain = CGRect.null
func report() {
    var b = CGDisplayBounds(id)
    if overlapsOthers(b) { park(); b = CGDisplayBounds(id) }
    let m = CGDisplayBounds(CGMainDisplayID())
    if b != last || m != lastMain {
        last = b; lastMain = m
        print("display \(id) bounds \(Int(b.origin.x)),\(Int(b.origin.y)) \(Int(b.width))x\(Int(b.height))"
              + " main \(Int(m.origin.x)),\(Int(m.origin.y)) \(Int(m.width))x\(Int(m.height))")
        fflush(stdout)
    }
}

park()
report()
signal(SIGTERM) { _ in exit(0) }
signal(SIGINT) { _ in exit(0) }
signal(SIGPIPE, SIG_IGN)
Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { _ in report() }
RunLoop.main.run()
