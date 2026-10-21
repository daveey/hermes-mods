// hermes-window-evict: move non-Chrome windows off the hidden agent virtual display onto the main display.
// Usage: hermes-window-evict X Y W H [--skip-pid N]...   (X Y W H = virtual display bounds, global CG coords)
// Prints one line per move: "evict <bundle> <title> from x,y to x,y"; prints "untrusted" and exits 0 when the
// calling process tree lacks Accessibility (TCC charges the supervisor's interpreter); "trusted" with --check.
import AppKit
import ApplicationServices

let args = Array(CommandLine.arguments.dropFirst())
if args.first == "--check" {
  print(AXIsProcessTrusted() ? "trusted" : "untrusted")
  exit(0)
}
guard args.count >= 4, let vx = Double(args[0]), let vy = Double(args[1]),
      let vw = Double(args[2]), let vh = Double(args[3]) else {
  FileHandle.standardError.write("usage: hermes-window-evict X Y W H [--skip-pid N]...\n".data(using: .utf8)!)
  exit(64)
}
var skip = Set<pid_t>([getpid()])
var i = 4
while i + 1 < args.count {
  if args[i] == "--skip-pid", let p = Int32(args[i + 1]) { skip.insert(p) }
  i += 1
}
let vd = CGRect(x: vx, y: vy, width: vw, height: vh)
let ownBundles: Set<String> = ["com.hermes.chrome-agent.notify"]

guard let infos = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] else { exit(0) }
var stuck: [(pid: pid_t, rect: CGRect)] = []
for w in infos {
  guard (w[kCGWindowLayer as String] as? Int) == 0,
        let pid = w[kCGWindowOwnerPID as String] as? pid_t, !skip.contains(pid),
        let bd = w[kCGWindowBounds as String] as? [String: CGFloat],
        let x = bd["X"], let y = bd["Y"], let ww = bd["Width"], let hh = bd["Height"] else { continue }
  let r = CGRect(x: x, y: y, width: ww, height: hh)
  if ww < 50 || hh < 50 || !r.intersects(vd) { continue }
  // Mostly on the virtual display only: a window straddling the shared corner by a few px is not stuck.
  let ov = r.intersection(vd)
  if ov.width * ov.height < 0.25 * ww * hh { continue }
  stuck.append((pid, r))
}
if stuck.isEmpty { exit(0) }
if !AXIsProcessTrusted() { print("untrusted"); exit(0) }

// Main display visible frame in CG (top-left origin) coordinates.
let primary = NSScreen.screens[0]
let vf = primary.visibleFrame
let mainTop = primary.frame.height - vf.maxY
let main = CGRect(x: vf.minX, y: mainTop, width: vf.width, height: vf.height)

for s in stuck {
  let bundle = NSRunningApplication(processIdentifier: s.pid)?.bundleIdentifier ?? "pid\(s.pid)"
  if ownBundles.contains(bundle) { continue }
  var v: CFTypeRef?
  let ax = AXUIElementCreateApplication(s.pid)
  guard AXUIElementCopyAttributeValue(ax, kAXWindowsAttribute as CFString, &v) == .success,
        let wins = v as? [AXUIElement] else { continue }
  for w in wins {
    var pv: CFTypeRef?, sv: CFTypeRef?
    var pos = CGPoint.zero, size = CGSize.zero
    guard AXUIElementCopyAttributeValue(w, kAXPositionAttribute as CFString, &pv) == .success,
          AXUIElementCopyAttributeValue(w, kAXSizeAttribute as CFString, &sv) == .success,
          AXValueGetValue(pv as! AXValue, .cgPoint, &pos),
          AXValueGetValue(sv as! AXValue, .cgSize, &size) else { continue }
    if abs(pos.x - s.rect.minX) > 2 || abs(pos.y - s.rect.minY) > 2 { continue }  // match by frame
    var t: CFTypeRef?
    AXUIElementCopyAttributeValue(w, kAXTitleAttribute as CFString, &t)
    // Fit: shrink if larger than the main display, then center-ish (slightly above middle).
    var newSize = size
    if size.width > main.width || size.height > main.height {
      newSize = CGSize(width: min(size.width, main.width), height: min(size.height, main.height))
      var sz = newSize
      AXUIElementSetAttributeValue(w, kAXSizeAttribute as CFString, AXValueCreate(.cgSize, &sz)!)
    }
    var np = CGPoint(x: main.minX + (main.width - newSize.width) / 2,
                     y: main.minY + max(0, (main.height - newSize.height) / 3))
    let r = AXUIElementSetAttributeValue(w, kAXPositionAttribute as CFString, AXValueCreate(.cgPoint, &np)!)
    print("evict \(bundle) \((t as? String) ?? "") from \(Int(pos.x)),\(Int(pos.y)) to \(Int(np.x)),\(Int(np.y))" +
          (r == .success ? "" : " (AX error \(r.rawValue))"))
  }
}
