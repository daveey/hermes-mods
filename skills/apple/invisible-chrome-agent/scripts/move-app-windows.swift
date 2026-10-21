// Move every window of an app (by bundle id) onto the main display via Accessibility, then activate it.
// Usage: swiftc -O move-app-windows.swift -o move-app-windows && ./move-app-windows <bundle-id> [x y]
// Needs Accessibility for the RESPONSIBLE process: run it from a gateway cron (Hermes Python.app), not
// from a Desktop/terminal shell, which prints "trusted false" and moves nothing.
import AppKit
import ApplicationServices
let a = CommandLine.arguments
guard a.count >= 2 else { print("usage: move-app-windows <bundle-id> [x y]"); exit(64) }
var p = CGPoint(x: a.count > 3 ? Double(a[2])! : 200, y: a.count > 3 ? Double(a[3])! : 80)
print("trusted", AXIsProcessTrusted())
guard let app = NSRunningApplication.runningApplications(withBundleIdentifier: a[1]).first else { print("not running"); exit(1) }
let ax = AXUIElementCreateApplication(app.processIdentifier)
var v: CFTypeRef?
let err = AXUIElementCopyAttributeValue(ax, kAXWindowsAttribute as CFString, &v)
guard err == .success, let wins = v as? [AXUIElement] else { print("windows err", err.rawValue); exit(2) }
for w in wins {
  var t: CFTypeRef?; AXUIElementCopyAttributeValue(w, kAXTitleAttribute as CFString, &t)
  let r = AXUIElementSetAttributeValue(w, kAXPositionAttribute as CFString, AXValueCreate(.cgPoint, &p)!)
  print("window", (t as? String) ?? "?", "move", r.rawValue)
}
app.activate()
