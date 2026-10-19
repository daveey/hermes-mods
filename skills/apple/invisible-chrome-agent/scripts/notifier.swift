// hermes-touchid-notify: a clickable "Hermes needs Touch ID" notification for hermes-chrome-agent.
//
// `display notification` (osascript) belongs to Script Editor: clicking it or its Show button opens Script
// Editor, so the hidden Chrome window asking for the passkey never comes forward. This tiny LSUIElement app
// (built into "Hermes Touch ID.app" by `hermes-chrome-agent install`) posts the notification itself with
// UNUserNotificationCenter, so the click comes back here.
//
// Protocol (line based, stdout/stdin), driven by the supervisor:
//   args:   --title T --body B [--timeout SECONDS]
//   stdout: "posted" | "error <msg>" | "action default" (body clicked) | "action show" (Show button)
//           | "action dismiss" | "timeout"
//   stdin after an action: "activate <pid>" -> this app (frontmost after the click) hands activation to that
//           pid; "exit" or EOF -> quit. SIGTERM removes the notification (request settled) and quits.
import AppKit
import UserNotifications

setvbuf(stdout, nil, _IOLBF, 0)

func emit(_ s: String) { print(s); fflush(stdout) }

func arg(_ name: String, _ dflt: String) -> String {
    let a = CommandLine.arguments
    if let i = a.firstIndex(of: name), i + 1 < a.count { return a[i + 1] }
    return dflt
}

let title = arg("--title", "Hermes needs Touch ID")
let body = arg("--body", "Tap Touch ID to approve a passkey")
let timeout = Double(arg("--timeout", "300")) ?? 300
let ident = "hermes-touchid-\(getpid())"

final class Delegate: NSObject, UNUserNotificationCenterDelegate {
    var answered = false

    func userNotificationCenter(_ c: UNUserNotificationCenter, willPresent n: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .list, .sound])
    }

    func userNotificationCenter(_ c: UNUserNotificationCenter, didReceive r: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        done()
        if answered { return }
        answered = true
        switch r.actionIdentifier {
        case UNNotificationDefaultActionIdentifier: emit("action default")
        case "show": emit("action show")
        default: emit("action dismiss"); exit(0)
        }
        // Wait for the supervisor to move the window onto the main display, then hand activation to Chrome:
        // after the click this app is the active one, so macOS lets it pass activation on.
        DispatchQueue.global().async {
            while let line = readLine() {
                let parts = line.split(separator: " ")
                if parts.first == "activate", parts.count > 1, let pid = Int32(parts[1]),
                   let app = NSRunningApplication(processIdentifier: pid) {
                    DispatchQueue.main.async {
                        app.activate(from: NSRunningApplication.current, options: [.activateAllWindows])
                        app.activate(options: [.activateAllWindows])
                        emit("activated \(pid)")
                    }
                } else if parts.first == "exit" {
                    break
                }
            }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { exit(0) }
        }
    }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let center = UNUserNotificationCenter.current()
let delegate = Delegate()
center.delegate = delegate

let show = UNNotificationAction(identifier: "show", title: "Show", options: [.foreground])
let category = UNNotificationCategory(identifier: "hermes-touchid", actions: [show], intentIdentifiers: [],
                                      options: [.customDismissAction])
center.setNotificationCategories([category])

if CommandLine.arguments.contains("--status") {
    // Prints notDetermined | denied | authorized | provisional | ephemeral (+ alert style) and exits.
    center.getNotificationSettings { s in
        let names: [UNAuthorizationStatus: String] = [.notDetermined: "notDetermined", .denied: "denied",
                                                      .authorized: "authorized", .provisional: "provisional"]
        let styles: [UNAlertStyle: String] = [.none: "none", .banner: "banner", .alert: "alert"]
        emit("\(names[s.authorizationStatus] ?? "other") style=\(styles[s.alertStyle] ?? "other")")
        exit(0)
    }
    app.run()
}

if !CommandLine.arguments.contains("--body") {
    // Relaunched by macOS for a click on a stale notification (its helper already exited): post nothing.
    DispatchQueue.main.asyncAfter(deadline: .now() + 3) { exit(0) }
    app.run()
}

signal(SIGTERM, SIG_IGN)
let term = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
var cancelled = false
var posted = false
term.setEventHandler {
    // Request settled. While macOS's first-run "Allow notifications?" prompt is still up (nothing posted
    // yet), stay until it is answered: quitting would withdraw the prompt and leave the app denied.
    cancelled = true
    if !posted { return }
    center.removeDeliveredNotifications(withIdentifiers: [ident])
    center.removePendingNotificationRequests(withIdentifiers: [ident])
    DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { exit(0) }
}
term.resume()

center.requestAuthorization(options: [.alert, .sound]) { ok, err in
    if !ok {
        emit("error not authorized: \(err?.localizedDescription ?? "denied in System Settings > Notifications")")
        exit(3)
    }
    if cancelled { exit(0) }
    let content = UNMutableNotificationContent()
    content.title = title
    content.body = body
    content.sound = UNNotificationSound(named: UNNotificationSoundName("Glass"))
    content.categoryIdentifier = "hermes-touchid"
    content.interruptionLevel = .timeSensitive
    center.add(UNNotificationRequest(identifier: ident, content: content, trigger: nil)) { err in
        if let err = err { emit("error \(err.localizedDescription)"); exit(4) }
        posted = true
        emit("posted")
        // The timeout starts once posted: the first run waits on macOS's "Allow notifications?" prompt, and
        // quitting while it is up withdraws it and leaves the app denied.
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout) {
            if !delegate.answered {
                center.removeDeliveredNotifications(withIdentifiers: [ident])
                emit("timeout")
                exit(0)
            }
        }
    }
}

app.run()
