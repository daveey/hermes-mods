---
name: messaging-bridges
description: "Use when connecting Slack/WhatsApp/Signal chat to Hermes."
version: 0.2.0
platforms: [macos]
---

# Messaging Bridges (Slack, WhatsApp, Signal)

Wire local CLIs/bridges to third-party chat networks using **persistent-session** auth so there is no repeat login. Each network has one durable pattern:

| Network | Mechanism | State lives in |
|---|---|---|
| WhatsApp | whatsmeow Multi-Device: scan QR from phone → session file | `whatsapp-bridge/store/whatsapp.db` |
| Signal | `signal-cli link` as secondary device: scan QR from Signal app | `~/.local/share/signal-cli/` |
| iMessage/SMS | `imsg` reads Messages.app directly | macOS Messages signed-in state |
| Slack | Hermes gateway Slack adapter, Socket Mode app (bot `xoxb-` + app `xapp-` tokens) | `~/.hermes/.env` |

Slack setup procedure: `references/slack-gateway-setup.md`.

User is terse and dislikes multi-choice menus — just execute the next concrete step and surface a single actionable artifact (QR image, URL, path) at a time. Don't ask "which first?" when the obvious order is: start the bridge → render QR → ask them to scan.

## Always-on rules

1. **Never shell-background with `nohup`/`disown`/`setsid`.** Hermes' terminal refuses those wrappers; use `terminal(background=true, notify=[...])` and watch the log with `notify` patterns.
2. **Render QRs as PNG, not ASCII, when delivering to chat.** Terminal-QR half-blocks mangle in message bubbles. Patch the bridge to also write the raw QR string to a file, then `qrencode -o qr.png -s 12 -m 3 < qr.txt` and deliver via `MEDIA:` path.
3. **Deliver one artifact per turn** when the user has to act on their phone. Show the QR, then stop talking; don't chain three unrelated prompts underneath.
4. **Session files are the whole value — back them up before rebuilding.** `store/whatsapp.db` and `~/.local/share/signal-cli/` are what makes auth persistent; wiping them forces a new QR scan.

## WhatsApp — `lharries/whatsapp-mcp` whatsmeow bridge

Clone, build, run. Session persists in `whatsapp-bridge/store/whatsapp.db`.

```bash
mkdir -p ~/hermes-bridges && cd ~/hermes-bridges
git clone --depth 1 https://github.com/lharries/whatsapp-mcp.git
cd whatsapp-mcp/whatsapp-bridge
go build -o whatsapp-bridge .
```

### Pitfall: `Client outdated (405) connect failure`

WhatsApp rejects stale `whatsmeow` client-version strings. The upstream repo's pinned version lags. Fix every time this error appears in the bridge log:

```bash
go get -u go.mau.fi/whatsmeow@latest
go mod tidy
```

Recent whatsmeow added `context.Context` as the first arg to several APIs; after `go mod tidy` the build will fail at ~5 call sites in `main.go`. Add `context.Background()` as the first argument to each:

- `client.Download(downloader)` → `client.Download(context.Background(), downloader)`
- `sqlstore.New("sqlite3", ..., dbLog)` → `sqlstore.New(context.Background(), "sqlite3", ..., dbLog)`
- `container.GetFirstDevice()` → `container.GetFirstDevice(context.Background())`
- `client.GetGroupInfo(jid)` → `client.GetGroupInfo(context.Background(), jid)`
- `client.Store.Contacts.GetContact(jid)` → `client.Store.Contacts.GetContact(context.Background(), jid)`

Then rebuild. These are mechanical — the compiler lists every site.

### Patch for chat-deliverable QR

In `main.go` the QR handler only prints ASCII. Add a raw-code dump next to the terminal render so you can `qrencode` it:

```go
if evt.Event == "code" {
    fmt.Println("\nScan this QR code with your WhatsApp app:")
    qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
    _ = os.WriteFile("qr.txt", []byte(evt.Code), 0644)
    fmt.Println("QR_RAW_WRITTEN")
}
```

Run flow (via `terminal(background=true, notify=["QR_RAW_WRITTEN","Successfully"])`):

```bash
cd ~/hermes-bridges/whatsapp-mcp/whatsapp-bridge
rm -rf store qr.txt whatsapp.log   # ONLY on first setup; preserves session otherwise
./whatsapp-bridge > whatsapp.log 2>&1
```

After `QR_RAW_WRITTEN`:

```bash
qrencode -o ~/hermes-bridges/whatsapp-qr.png -s 12 -m 3 < qr.txt
```

Deliver the PNG via `MEDIA:/Users/.../whatsapp-qr.png` and tell the user: WhatsApp → Settings → Linked Devices → Link a Device.

### Pitfall: QR auto-rotates every ~20s inside the SAME bridge run

The `evt.Event == "code"` branch fires repeatedly while unpaired — whatsmeow rotates the pairing code roughly every 20 seconds. The ASCII QR you see in `whatsapp.log` is a historical dump of past rotations, not what the user's phone will accept right now. Only `qr.txt` holds the live code (your patch overwrites it on every rotation). When the user is slow to scan, **re-render the PNG from the current `qr.txt` and resend — do NOT restart the bridge**. Restarting resets the device-side pairing session and wastes a device slot.

### Pitfall: "I scanned, device didn't link"

If the user says they scanned successfully but `store/whatsapp.db` mtime hasn't advanced and the log has no `Successfully paired`, the PNG they scanned was from a prior rotation. Re-render from the current `qr.txt` and ask them to retry. Do not add new bridges or new patches.

### Fallback: phone pair code (no QR)

When the user cannot scan (bad camera, repeated stale-PNG failures), whatsmeow supports an 8-char code the user types in WhatsApp instead. Gate on an env var so default QR flow is unchanged:

```go
if phone := os.Getenv("WHATSAPP_PAIR_PHONE"); phone != "" {
    client.Connect()
    code, err := client.PairPhone(context.Background(), phone, true,
        whatsmeow.PairClientChrome, "Chrome (Hermes Mac)")
    if err != nil { logger.Errorf("Failed to request pair code: %v", err); return }
    fmt.Printf("\nPAIR_CODE: %s\n", code)
    _ = os.WriteFile("pair_code.txt", []byte(code), 0644)
    go func() {
        qch, _ := client.GetQRChannel(context.Background())
        for evt := range qch { if evt.Event == "success" { connected <- true; return } }
    }()
}
```

Launch with `WHATSAPP_PAIR_PHONE="<E164-without-plus>"`, e.g. `16506446875` not `+16506446875`. User: WhatsApp → Linked Devices → Link a Device → **Link with phone number instead** → types code.

### Pitfall: `PairPhone` returns `400 bad-request`

Almost always: the WhatsApp account is at the 4-device limit, with stale pairings from earlier QR attempts counting as live slots. Tell the user to open WhatsApp → Settings → Linked Devices and delete every entry that isn't the current session (`Chrome`, `Mac`, `Firefox`, `Linux`, etc.), then retry. Toggling `+` on/off in the phone string does NOT fix 400 — don't burn a cycle on it.

On successful pair the bridge logs `Successfully connected and authenticated!`. Independent check: `store/whatsapp.db` mtime bumps past the pre-pairing timestamp. From then on just relaunching `./whatsapp-bridge` resumes the session — no QR.

## Signal — `signal-cli` secondary device

```bash
brew install signal-cli qrencode
# Launch via terminal(background=true, pty=true, notify=["sgnl:", "Associated with:"])
signal-cli link -n "hermes-mac" 2>&1 | tee ~/hermes-bridges/signal-link.log
```

Stdout's first line is a `sgnl://linkdevice?uuid=...&pub_key=...&capabilities=...` URI (NOT `tsdevice:/` — that is an older format no longer emitted). Extract and render:

```bash
SIG=$(grep -oE 'sgnl://[^ ]+' ~/hermes-bridges/signal-link.log | head -1)
qrencode -o ~/hermes-bridges/signal-qr.png -s 12 -m 3 "$SIG"
```

Deliver PNG. User: Signal app → Settings → Linked Devices → + → scan. The `link` process blocks until paired, then prints `Associated with: +<E164>` and exits 0. That line is the only reliable success signal — the `sgnl:` match in notify fires on QR emission, not on success, so watch both.

**`pty=true` is required** on the terminal launch — signal-cli expects a TTY for its prompt handling and will hang silently without one. State lands in `~/.local/share/signal-cli/data/`.

### Usage after link

```bash
signal-cli listAccounts                                     # confirms +<E164>
signal-cli -a +<E164> send -m "test" +<E164>                # self-send verification
signal-cli -a +<E164> receive                               # pulls pending, idempotent
```

Every subsequent command needs `-a +<E164>`; signal-cli does not auto-select when multiple accounts are registered.

### Pitfalls

- `Link request timed out, please try again.` means the sgnl:// URI expired (~5min). Re-run `signal-cli link` — the old PNG is permanently dead.
- Do NOT run `signal-cli register` for a linked-device setup. That path creates a brand-new standalone Signal account and triggers SMS verification. Only use `link`.
- `INFO ... The Signal protocol expects that incoming messages are regularly received.` is informational, not an error. Ignore it.

## iMessage/SMS

Use the `imessage` skill (`imsg` CLI). No pairing. One check up-front: `imsg chats --limit 1 --json` — if it errors with a permission diagnostic, Full Disk Access needs to be granted to the terminal binary that actually runs (on cmux.app this is **ghostty**, not cmux). After granting, fully quit the terminal and relaunch; the entitlement is read at launch.
