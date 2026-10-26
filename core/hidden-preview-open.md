# Hidden-session preview opens: mounted, ready, then acknowledged

Patches:

- `patches/0118-fix-desktop-mount-hidden-session-preview-opens-befor.patch`: backend + desktop. `preview.open`
  becomes a server request answered by one chosen Desktop window, which mounts the tab (parked, if its chat is
  off screen) before it says yes.
- `patches/0119-fix-desktop-hidden-preview-opens-wait-for-a-ready-gu.patch`: desktop. The window waits for the
  page's guest to reach `dom-ready` (or fail) before it answers, and an open from another profile's chat is hosted
  off view instead of refused.

What you see: an agent in a chat you are not looking at calls `desktop_preview(action="open")`, then
`read` or `act`. Before, the open was a fire-and-forget event: the tool said `success` even with no window, the tab
was only stored for a later render, and the next read failed ("preview webview is not ready", or nothing to read).
Now the open returns success only once the page is loaded in a real (parked) guest, or a real error: no Desktop
window, mount timeout, or the page's load error (for example `ERR_NAME_NOT_RESOLVED`). Your rail and drawer don't
change. When you switch to that chat, you see the same page with its state, not a reload.

## Behavior

- **One window answers.** The backend sends `preview.open` to exactly one Desktop peer. If no capable peer exists,
  the tool returns "No Hermes Desktop window acknowledged opening the preview. Open or update the Desktop app,
  then retry." It never reports success without an answer.
- **The tool returns the renderer's verdict.** `{success: true, url, label}` or `{success: false, error}` comes
  from the window. The tool waits up to 15 s. The window gives up after 12 s, so its own timeout error arrives first.
- **Hidden chats mount parked.** The tab mounts off screen and inert, without stealing focus or changing
  `$visiblePreviewTabs`. Follow-up `preview.read` / `preview.act` from that chat reach it.
- **What counts as ready (0119).** For web targets (URLs and HTML previews, but not data URLs or transient
  targets), the open waits for a `dom-ready` newer than the open. If a reused tab already shows that address, its
  current state counts. A main-frame `did-fail-load`, or a `loadURL` rejection other than `ERR_ABORTED`, fails the
  open with that error. File previews (no guest) only need the pane mounted (0118).
- **Other profiles (0119).** If the chat belongs to a profile that is not in view, the tab goes into that profile's
  own bucket and mounts parked in `$offViewPreviewTabs`. The viewed profile's rail, drawer, selection and pins never
  see it. That profile's agent can still read and act on it. Switching to that profile and chat shows the same guest.
- **Real viewport for born-parked pages (0119).** A pane mounted parked before it was ever shown gets a
  1280×800 kept size. Without it the size is 0×0, and trusted input never lands in a 0×0 guest.
- **Bounded.** At most 8 agent-requested hidden mounts (`HIDDEN_LIVE_PREVIEW_LIMIT`) are kept. Older ones are
  evicted and stay unmounted until opened again. Tabs restored from storage stay lazy and are not mounted at startup.
- No config keys.

## How it works

1. **Tool (0118).** `open_preview_tool` calls `server_requests.send("preview.open", sid, {url, label},
   timeout=15)` and returns the parsed `value`. `desktop_preview` `open` goes through it. A `preview.open`
   contract (`PreviewOpenRequestParams`: `session_id`, `url`, `label=""`) is added to the server-request contracts
   and the generated TS/OpenRPC.
2. **Routing (0118).** `send` asks `_preview_client(sid)` (bound to `_preview_request_client`) for a single target.
   It tries the session's own live transports first, then the live transports sorted by `id()` as a stable
   fallback. A peer qualifies if it is live, answers requests, and has the same auth identity as the session. The
   chosen peer is attached to the session, so later read/act requests use it. The frame is written only to that
   peer. Answers from any other transport are ignored. A "not shown" decline does not settle a targeted request.
   `open_requests` leaves targeted requests out of replay, so reconnecting windows don't create duplicate tabs. A
   failed write resolves the request with "Desktop window disconnected".
3. **Renderer (0118).** The new `preview.open` handler in `server-requests.ts` resolves the target, calls
   `openPreview(target, storedSession, runtimeId, profile, ownTabOnly=true)` (which now returns the tab id), and
   polls until the surface is ready. `ownTabOnly` skips pinned tabs and records the id in `$requestedPreviewMounts`.
   The pane mirror's new `initiallyParked` input registers those tabs as parked panes without adopting them into a
   zone. `KeepAlivePanes` gives a parked pane with no placement a hidden placement, so its body mounts.
   `keepsLiveBody` treats requested tabs as live. `PreviewTilePane` registers itself in `preview-mount.ts`.
4. **Guest readiness (0119).** `PreviewPane` publishes `loading` / `ready` / `failed` to `preview-guest-ready.ts`.
   Each change gets a global epoch. The handler takes the epoch before `openPreview`, and `waitForPreviewSurface`
   accepts only a state with a newer epoch. If the URL is unchanged, it accepts any epoch. The script-runner check
   from 0118 (`hasPreviewScriptRunner`) is removed, because the runner registers before Electron attaches the guest.
5. **Off-view profiles (0119).** `openPreview` with `ownTabOnly` and a profile other than `viewKey` calls
   `openOffViewPreview`. It reuses the owner's non-pinned tab in that bucket or mints a new one, then persists it
   and requests the mount. `syncOffViewTabs` (run from `persistTabs` and on mount-request changes) derives
   `$offViewPreviewTabs`. `applyPreviewScope` publishes the union first, so a parked tab is never missing from both
   lists, which would destroy its guest. `previewTabsFor` adds the owner's own off-view tabs.
   `previewTabById` and `PreviewTilePane` resolve tabs from both lists. The handler's owner profile is now always
   `previewScopeForRuntime(sessionId)`, which matches what read/act use.

## Code

Backend (0118):

- `tools/open_preview_tool.py`: `open_preview_tool` (server request instead of `desktop_ui.emit_or_error`).
- `tui_gateway/server_requests.py`: `ServerRequest.target`, `bind_sinks(..., preview_client)`, `_register`
  (targeted write), `send` (preview routing), `resolve_response` (target-only answers), `open_requests` (no replay).
- `tui_gateway/session_transports.py`: `_preview_request_client(sid)`.
- `tui_gateway/server.py`: binds `_preview_request_client`.
- `tui_gateway/contracts/server_requests.py`: `PreviewOpenRequestParams`, `server_request("preview.open", ...)`.
- `apps/shared/src/gateway-contract.generated.ts`, `gateway-contract.openrpc.json`: regenerated.

Desktop:

- `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event/server-requests.ts`: `previewOpen` (0118),
  `waitForPreviewSurface`, `PREVIEW_OPEN_READY_MS = 12_000` (0119).
- `apps/desktop/src/store/preview.ts`: `$requestedPreviewMounts`, `openPreview(..., ownTabOnly)` returning the id
  (0118); `$offViewPreviewTabs`, `offViewTabsFor`, `syncOffViewTabs`, `previewTabById`, `openOffViewPreview`,
  `applyPreviewScope` / `previewTabsFor` changes (0119).
- `apps/desktop/src/app/chat/pane-mirror.ts`: `initiallyParked` option, `registerTiles` (0118).
- `apps/desktop/src/app/chat/preview-tile.tsx`: `keepsLiveBody`, `initiallyParked` source (0118; 0119 adds
  off-view tabs and uses `previewTabById`).
- `apps/desktop/src/components/pane-shell/tree/renderer/keep-alive-panes.tsx`: hidden placements for parked panes
  (0118); `bornParked` with `BORN_PARKED_WIDTH/HEIGHT` 1280px/800px (0119).
- `apps/desktop/src/app/chat/right-rail/preview-mount.ts` (new, 0118): `registerPreviewPaneMount`,
  `hasPreviewPaneMount`.
- `apps/desktop/src/app/chat/right-rail/preview-guest-ready.ts` (new, 0119): `setPreviewGuestState`,
  `touchPreviewGuestReady`, `clearPreviewGuestState`, `previewGuestState`, `previewGuestEpoch`.
- `apps/desktop/src/app/chat/right-rail/preview-pane.tsx` (0119): publishes guest state on create, `dom-ready`,
  `did-fail-load`, navigation and unmount.
- `apps/desktop/src/app/chat/right-rail/preview.tsx`: registers the mount (0118); resolves off-view tabs (0119).
- `apps/desktop/src/app/chat/right-rail/preview-script-runner.ts`: `hasPreviewScriptRunner` added in 0118 and
  removed in 0119.

## Tests

All passing in the patched tree (Oct 17 2026 PT).

Python (`scripts/run_tests.sh`):

- `tests/tools/test_preview_open_ack.py`: `test_open_without_a_window_cannot_report_success` (no peer: an error,
  nothing written); `test_open_returns_only_the_selected_renderers_ack` (both success and failure answers pass through
  unchanged; one frame with normalized url and trimmed label; the request is not in `open_requests` while pending).
- `tests/tools/test_desktop_tools_diet.py`: `test_open_is_acknowledged_and_close_emits` (`desktop_preview` open
  goes through `server_requests.send`; close still emits an event).
- `tests/tools/test_open_preview_tool.py`: a send failure is reported; existing directories are refused without a
  request; files, https URLs and missing paths are requested.
- `tests/tui_gateway/test_preview_open_routing.py`: `test_preview_owner_then_stable_fallback` (the session's peer
  wins; a hidden session gets a stable fallback that is then attached; a `FanoutTransport` resolves to its owner; a
  closed peer is skipped); `test_no_capable_or_same_identity_window_is_unavailable`.
- `tests/tui_gateway/contracts/test_generated.py`: the contract scan now includes `tools/*.py`, so the new
  request is covered. `preview.open` stays a known event name for older backends.

Desktop (`cd apps/desktop && npx vitest run <files>`):

- `src/app/chat/preview-open-readiness.test.tsx` (0119, real `PreviewPane`):
  - `acknowledges a hidden open only once its guest is ready, then reads it`: no answer while the runner is
    registered but there is no `dom-ready`; the host is 1280px; after `dom-ready` it answers success, and read
    returns the page text.
  - `fails a hidden open whose main-frame load fails instead of acknowledging it`: the answer carries
    `ERR_NAME_NOT_RESOLVED`.
  - `hosts another profile's hidden open off view and keeps the same guest when that chat is focused`: beta's
    `$previewTabs` is unchanged, the guest is inert, and alpha's read works. Focusing alpha shows the same connected
    webview, not inert. Leaving again keeps it mounted.
- `src/app/chat/preview-tile-live-body.test.tsx`: `mounts a hidden-born Browser offscreen and preserves its guest on
  focus`. The open is answered only after the render, the pane is parked and inert, `preview.read` and `preview.act`
  reach it, and the same guest is shown on focus.
- `src/app/chat/preview-tile.test.ts`: `does not eagerly mount hidden restored tabs`; `does not remount cap-evicted
  background opens on unrelated updates` (9 opens keep 8; reopening the evicted one mounts it again).
- `.../gateway-event/server-requests.test.ts`: `acknowledges a mounted text file without requiring a browser script
  runner` (file path); `fails instead of acknowledging a tab that never mounts` (12 s timeout).

Untested:

- Reused tab already showing the requested URL (`unchanged`, `touchPreviewGuestReady`).
- A `loadURL` rejection, and the `ERR_ABORTED` exemption.
- "Desktop window disconnected" on a failed targeted write.
- Ignoring answers from a non-target transport.
- `migratePreviewTabsForProfile` and `dropPreviewTabsForProfile` with off-view tabs.

The 0×0-viewport claim was checked once in an isolated Electron run (commit message). There is no automated test
for it.

## Applying

- 0118 applies on top of 0001–0117. Its context includes 0015's background preview routing
  (`backgroundPreviewTabFor` in `server-requests.ts`), so apply 0015 first.
- 0119 needs 0118. It edits 0118's `previewOpen`, `initiallyParked` and `KeepAlivePanes` code.
- verified: applies with git am -3 --empty=keep on top of 0001–0118 at merge-base 4957bbd0a0, Oct 17 2026 PT.

## Gotchas

- `core/session-takeover.md` (0015) covers the other half: a hidden chat's `preview.read` / `preview.act` reaching
  its parked tab. These patches make open create that parked tab and wait for it to be ready.
- Version skew: a new backend with an older Desktop that has no `preview.open` request handler gets no usable
  answer, and the tool returns its "update the Desktop app" error. An older backend still emits the `preview.open`
  event, and current renderers still decode it.
- Opens are never fanned out. With several windows, the page lives in the one that was chosen, and that window is
  stuck to the session. Focusing the chat in a different window does not move the page there.
- The fallback peer requires the same auth identity. A session with an auth user and no matching window is
  refused, even if other windows are open.
- The 12 s / 15 s budget covers loading too. A slow page can fail the open while it keeps loading in its parked tab.
- Off-view tabs are excluded from the viewed profile's rail, so you only see them after you switch to their profile
  and chat.
- An evicted hidden mount loses its guest state. The next open of that tab mounts a fresh guest.

## Plugin?

No. The change is in the core server-request rail (targeted send, replay, answer matching), the session transport
selection, the gateway contract, and the Desktop pane mirror, keep-alive host and preview store. None of these
has a plugin hook.
