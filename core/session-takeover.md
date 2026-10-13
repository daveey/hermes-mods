# Session takeover: "Take over here" + hidden chats keep driving their Browser page

Patches:

- `patches/0013-feat-sessions-take-over-a-chat-open-in-another-Herme.patch`: backend. New `session.take_over`
  JSON-RPC and a revocation watcher. One backend can now move a chat's active-session lease away from another
  **live** Hermes process.
- `patches/0014-feat-desktop-Take-over-here-on-a-chat-open-in-anothe.patch`: desktop. The "This chat is open
  somewhere else" error card gets a **Take over here** button.
- `patches/0015-feat-desktop-a-hidden-chat-s-agent-keeps-driving-its.patch`: desktop. When you switch away from a
  chat, its agent can still read and act in that chat's own parked Browser tab.

What you see: you open a chat that another Hermes window is holding. Stock Hermes refuses every send with
`SESSION_NOT_OWNED`, for example when a second Desktop app's backend on the same machine has it open. With these
patches the error card shows **Take over here** before **Start new session**. Click it and this window gets the chat:
the other window stops its turn and shows "This chat was taken over by another Hermes window.", the transcript reloads
from disk, and your refused prompt is sent again. Separately (0015), a long browser task keeps running after you
switch to a different chat. Before, the agent's `preview.read`/`preview.act` calls were declined as soon as its chat
was off screen.

## Behavior

### Take over here (0013 + 0014)

- **Shown only for `SESSION_NOT_OWNED`.** `errorRecoveryPlan` sets `takeOver: true` for that code alone. That plan is
  `{ retry: false, startNewSession: true, takeOver: true }`, so Retry stays hidden.
- **Button order:** Choose model, then **Take over here**, then Start new session, and so on.
- **Card body text** for `SESSION_NOT_OWNED` (en): "This chat is open in another Hermes window or terminal. Take it
  over here, or start a new chat."
- **Click flow:**
  1. `session.take_over({session_id: <runtime id>})`, routed to the owning profile.
  2. If the result has `acknowledged: false`, a warning toast says "The other window didn't confirm; it may still be
     finishing." The flow continues anyway.
  3. `resumeTile(storedId, {refreshTranscript: true})`. This is best effort: a failure here is ignored.
  4. `aui.message().reload()`, the same call the Retry button makes, so the refused prompt runs again.
  5. If `session.take_over` throws, an error toast "Could not take over this chat" appears and nothing is re-run.
  6. The button is disabled while the request is in flight.
- **The displaced window** (old owner):
  - Within about 2 s its turn is hard-interrupted.
  - Open clarify, approval and sudo prompts are cancelled with outcome `cancelled`, so the agent thread unblocks.
  - It gets an `error` event: `{message: "This chat was taken over by another Hermes window.", error_surface:
    {layer: "gateway", code: "SESSION_NOT_OWNED", retryable: false}}`. Its card therefore offers Take over here too,
    which lets you take the chat back.
  - Its next submit is refused with `SESSION_NOT_OWNED`.
- **RPC results:**
  - `{status: "already_owner", previous_owner: null, acknowledged: true}` when this runtime already holds the lease.
    Nothing changes.
  - `{status: "taken_over", acknowledged, previous_owner: {surface, pid, started_at}}` on success. `acknowledged` is
    true when every displaced owner wrote an ack, or when its pid is dead.
  - Error `4090` with `data.reason` on refusal:
    - `SESSION_COORDINATION_UNAVAILABLE` when the registry is unreadable (fail closed; the file is never
      overwritten).
    - `MAX_CONCURRENT_SESSIONS` only when there was no owner to displace and the cap is full.
  - Error `4001` for an unknown session.
- **Environment variables** (none are config.yaml keys):

  | Variable                                | Default | Meaning                                                                  |
  |-----------------------------------------|---------|--------------------------------------------------------------------------|
  | `HERMES_SESSION_TAKEOVER_ACK_TIMEOUT_S` | `8`     | How long `session.take_over` waits for the old owner's ack.              |
  | `HERMES_SESSION_TAKEOVER_POLL_S`        | `2`     | Watcher poll period. `<= 0` disables the watcher. It is also off when `HERMES_TEST_ISOLATION` is set. |

### Hidden chat keeps its Browser page (0015)

- Say you start a browser task in chat A and then switch to chat B. A's Browser tab leaves the layout but stays
  mounted ("parked"). The window holding that parked page now answers A's `preview.read` and `preview.act`.
- **It only ever acts in A's own non-pinned tab.** The tab it picks:
  1. the tab A last had in front (`rememberedPreviewTabId`)
  2. otherwise A's first `url` tab
  3. otherwise A's first tab
- **Never a pinned tab.** Pins render in every session, so the user may be looking at one. If A only has a pin, the
  request is still declined.
- **Not a background request when it's on screen.** If any of A's own tabs is visible, normal focus/hover resolution
  applies.
- Declined, as before, when:
  - this window has no live page surface for A (`hasLivePreviewSurface`)
  - the window is a popped-out Browser window (`isBrowserWindow()`)
- `tour` and `terminal.read` still follow the rule that the session must be shown.

## How it works

### Background: active-session leases (stock)

`hermes_cli/active_sessions.py` keeps a registry at `<HERMES_HOME>/runtime/active_sessions.json`, guarded by a file
lock (`active_sessions.lock`). Each entry holds:

- `lease_id`, `session_id` (the stored session key), `surface`
- `pid` and `process_start_time`
- `metadata.live_session_id`, the gateway runtime id

`try_acquire_active_session` refuses with `SESSION_NOT_OWNED` when a different, live writer holds the key. "Same
writer" means the same pid plus the same `live_session_id`.

`tui_gateway/server.py::_ensure_active_session_slot(sid, session)` claims the lease on submit and stores it at
`session["active_session_lease"]`.

Stock upstream already has `_take_over_detached_runtime_lease` in `tui_gateway/session_lifecycle.py`. It moves a lease
from a client-less sibling runtime in the **same process**. Nothing in stock upstream handles a live foreign pid.

### 0013 backend

1. **`take_over_active_session(...)`** in `hermes_cli/active_sessions.py`. Under the same lock and the same strict read
   as `try_acquire`:
   - It removes every live entry for the key that is not the same writer.
   - It writes one entry for this process with `metadata.took_over_from=[old lease ids]` and `took_over_at`.
   - The concurrent-session cap is skipped when something was displaced, because the net slot count is unchanged.
   - A same-writer entry is re-entrant.
   - It returns `(lease, removed_entries, refusal)`.
2. **Helpers:**
   - `find_takeover_of(lease_id)`: a lock-free, tolerant read that returns the entry naming the lease in
     `took_over_from`.
   - `acknowledge_takeover(old_lease_id)`: under the lock, appends to the new entry's `metadata.handoff_acked`.
   - `takeover_acknowledged(new_lease, old_entries)`: true when every old lease is acked or its pid is dead.
3. **`tui_gateway/methods_session_takeover.py`** (new). It is registered from `methods_session_interrupt.register()`.
   Its bodies are rebound onto `server.py` globals with `method_ctx.bind_module`, so they refer to `_sessions`,
   `_emit`, `_interrupt_session_turn`, `_attach_lease`, `_lease_metadata`, `_load_durable_truncation_history`, etc.
   without qualification.
   - The **`session.take_over` handler**, decorated `@_registry.profile_scoped`:
     1. Returns `already_owner` if the runtime already holds the lease.
     2. Tries the in-process `_take_over_detached_runtime_lease`.
     3. Otherwise calls `take_over_active_session`, then `_attach_lease`.
     4. Runs one immediate `_takeover_watch_pass()`, so a same-process sibling stands down at once.
     5. Calls `_await_takeover_ack` (polls every 0.25 s up to the timeout).
     6. Calls `_reload_history_after_takeover`, which re-reads state.db through `_load_durable_truncation_history` +
        `canonicalize_replay_history` and bumps `history_version`. It is skipped while a turn is running.
   - The **watcher** `_start_takeover_watcher()` runs a daemon thread `session-takeover-watch` started with
     `agent.memory_provider.spawn_context_thread`. Each poll, `_takeover_watch_pass()`:
     - groups held leases by registry path
     - **stats** the registry file `(inode, mtime_ns, size)` and skips the read if nothing changed
     - calls `find_takeover_of` for each held lease
   - **`_revoke_taken_over_lease`**, run when a held lease turns out to be taken over:
     1. Under `_sessions_lock`: marks the lease released, pops it from the session, and sets
        `session["_lease_taken_over"] = True`. Stock finalize honours that flag and does not end the row the new owner
        is writing. The old registry entry is already gone, and the new owner's entry is never touched.
     2. Calls `_interrupt_session_turn`.
     3. Calls `server_requests.cancel(sid, reason="taken_over")`.
     4. Emits the `error` event.
     5. Calls `acknowledge_takeover`.
4. **Contracts:**
   - `tui_gateway/contracts/sessions.py` adds `TakeOverStatus`, `SessionPreviousOwner`, `SessionTakeOverResult` and
     `method("session.take_over", params=SessionParams, ...)`.
   - `tui_gateway/contracts/events.py` gives `ErrorPayload` an optional `error_surface: ErrorSurface | None`, plus
     `ErrorPayload.model_rebuild()` after `ErrorSurface` is declared.
   - The generated TS and OpenRPC files are regenerated.
5. `server.py`: `"session.take_over"` is added to `_LONG_HANDLERS`. It blocks for up to 8 s, so it must run on the RPC
   pool and not block the reader.

### 0014 desktop

- `apps/desktop/src/store/session-states.ts`: optional `SessionTileDelegate.takeOverSession(runtimeId)`.
- `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts` implements it. When the runtime maps to a stored
  session, it uses `requestForStoredSession` (owner-profile routing, the same as interrupt); otherwise it uses
  `requestGateway`.
- `apps/desktop/src/lib/error-surface.ts`: `ErrorRecoveryPlan.takeOver`.
- `.../gateway-event/status.ts`: new `gatewayErrorSurface()`. A gateway `error` event's own `error_surface` (parsed
  with `parseErrorSurface`) now wins over guessing from the message text. That is how the watcher's event gets the
  `SESSION_NOT_OWNED` card.
- `apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx`:
  - new `TakeOverAction`
  - the Retry, limit-reset and scheduled-retry buttons move into a `RetryActions` component, which keeps
    `ErrorRecoveryActions` under its complexity cap

### 0015 desktop

- `apps/desktop/src/store/preview.ts`: `rememberedPreviewTabId(sessionId)` reads `activeTabBySession`.
- `apps/desktop/src/app/chat/right-rail/preview-active-tab.ts`: `backgroundPreviewTabFor(owner)`. Both
  `activePreviewTabFor` and `readActivePreview` (`preview-reader.ts`) try it first.
- `.../gateway-event/server-requests.ts`:
  - `BACKGROUND_PREVIEW_REQUESTS = {'preview.act', 'preview.read'}` and `windowHoldsBackgroundPreview(sessionId)`.
  - In `handleServerRequest`, when the route is `'ignore'` (this window isn't showing the session), a background
    preview request is claimed and run with `isActiveSession: false`.
  - `previewAct` now refuses only when `!isActiveSession && !backgroundPreviewTabFor(owner)`.

## Code

The excerpts below are the core of each patch. See the patch files for the complete diff.

`hermes_cli/active_sessions.py` (0013), the takeover itself:

```python
def take_over_active_session(*, session_id, surface, metadata=None, registry_home=None,
                             track_liveness=False, config=None):
    key = str(session_id or "")
    ...
    with _FileLock(lock_path):
        try:
            loaded = _read_live_entries(state_path, track_liveness=track_liveness, warn="...",
                                        target_session_id=key)
        except ActiveSessionRegistryError:
            loaded = None
        if loaded is None:
            return None, [], ActiveSessionRefusal("...cannot safely move this session here...",
                                                  SESSION_COORDINATION_UNAVAILABLE)
        entries = loaded[1]
        held = [e for e in entries if str(e.get("session_id") or "") == key]
        owned = [e for e in held if not _is_same_writer(e, metadata)]
        if held and not owned:                      # re-entrant: same writer
            entry = _lease_entry(lease_id=lease_id, session_id=key, surface=str(surface),
                                 metadata=metadata, track_liveness=track_liveness)
            _write_entries(state_path, [*[e for e in entries if e.get("session_id") != key], entry])
            return lease, [], None
        if not held:                                # plain acquire: cap applies
            max_sessions = resolve_max_concurrent_sessions(config)
            if max_sessions is not None and len(entries) >= max_sessions:
                _write_entries(state_path, entries)
                return None, [], ActiveSessionRefusal(..., MAX_CONCURRENT_SESSIONS)
        took_over_from = [str(e.get("lease_id") or "") for e in owned]
        md = dict(metadata or {})
        if owned:
            md.update(took_over_from=took_over_from, took_over_at=time.time())
        entry = _lease_entry(lease_id=lease_id, session_id=key, surface=str(surface),
                             metadata=md or None, track_liveness=track_liveness)
        kept = [e for e in entries if str(e.get("session_id") or "") != key] + [entry]
        _write_entries(state_path, kept)
    return lease, owned, None

def takeover_acknowledged(new_lease, old_entries) -> bool:
    if not old_entries:
        return True
    own = next((e for e in _read_entries(Path(new_lease.state_path))
                if str(e.get("lease_id") or "") == new_lease.lease_id), None)
    md = (own or {}).get("metadata")
    acked = set((md.get("handoff_acked") or []) if isinstance(md, dict) else [])
    return all(str(o.get("lease_id") or "") in acked
               or _pid_liveness(o.get("pid"), o.get("process_start_time"), lenient=True) is False
               for o in old_entries)
```

`tui_gateway/methods_session_takeover.py` (0013), the handler and the revocation:

```python
_TAKEOVER_MESSAGE = "This chat was taken over by another Hermes window."
_TAKEOVER_ERROR_SURFACE = {"layer": "gateway", "code": "SESSION_NOT_OWNED", "retryable": False}

def _revoke_taken_over_lease(sid, session, lease, taker) -> bool:
    from hermes_cli.active_sessions import acknowledge_takeover
    with _sessions_lock:
        if lease.released or session.get("active_session_lease") is not lease:
            return False
        lease.released = True
        session.pop("active_session_lease", None)
        session["_lease_taken_over"] = True   # finalize must not end the row the new owner writes
    _interrupt_session_turn(sid, session, request_id="lease-taken-over")
    server_requests.cancel(sid, reason="taken_over")       # unblock clarify/approval/sudo
    _emit("error", sid, {"message": _TAKEOVER_MESSAGE, "error_surface": dict(_TAKEOVER_ERROR_SURFACE)})
    acknowledge_takeover(lease.lease_id, state_path=lease.state_path)
    return True

@method("session.take_over")
@_registry.profile_scoped
def _(rid, params: dict) -> dict:
    session, err = _sess_nowait(params, rid)
    if err:
        return err
    sid = str(params.get("session_id") or "")
    if session.get("active_session_lease") is not None:
        return _ok(rid, {"status": "already_owner", "previous_owner": None, "acknowledged": True})
    key = str(session.get("session_key") or "")
    surface = _session_source(session)
    if key and _take_over_detached_runtime_lease(sid, session, key):      # same-process sibling
        _reload_history_after_takeover(session)
        return _ok(rid, {"status": "taken_over", "acknowledged": True,
                         "previous_owner": {"surface": surface, "pid": os.getpid(), "started_at": None}})
    from hermes_cli.active_sessions import take_over_active_session
    try:
        lease, removed, refusal = take_over_active_session(
            session_id=key, surface=surface, metadata=_lease_metadata(sid),
            registry_home=session.get("profile_home"), config=_load_cfg(),
            track_liveness=str(surface or "").strip().lower() == "desktop")
    except Exception:
        return _err(rid, 4090, _SESSION_OWNERSHIP_UNAVAILABLE, {"reason": "SESSION_COORDINATION_UNAVAILABLE"})
    if refusal is not None:
        reason = getattr(refusal, "reason", None)
        return _err(rid, 4090, str(refusal), {"reason": reason} if reason else None)
    _attach_lease(session, lease)
    with contextlib.suppress(Exception):
        _takeover_watch_pass()
    acknowledged = _await_takeover_ack(lease, removed)
    _reload_history_after_takeover(session)
    return _ok(rid, {"status": "taken_over", "acknowledged": acknowledged,
                     "previous_owner": _takeover_previous_owner(removed[0] if removed else None)})

def register(server) -> None:
    bind_module(globals(), server, skip=("_",))
    server._start_takeover_watcher()
```

`tui_gateway/contracts/sessions.py` (0013):

```python
class TakeOverStatus(WireEnum):
    taken_over = "taken_over"
    already_owner = "already_owner"

class SessionPreviousOwner(Result):
    surface: str
    pid: int | None = None
    started_at: float | None = None

class SessionTakeOverResult(Result):
    status: TakeOverStatus
    previous_owner: SessionPreviousOwner | None = None
    acknowledged: bool

method("session.take_over", params=SessionParams, result=SessionTakeOverResult, doc="...")
```

`apps/desktop/src/components/assistant-ui/thread/assistant-message.tsx` (0014):

```tsx
const TakeOverAction: FC<{ label: string }> = ({ label }) => {
  const { t } = useI18n(); const copy = t.assistant.thread
  const aui = useAui(); const view = useSessionView()
  const sessionId = useStore(view.$runtimeId); const storedSessionId = useStore(view.$storedId)
  const [pending, setPending] = useState(false)
  const takeOver = useCallback(async () => {
    const delegate = sessionTileDelegate()
    if (!sessionId || !delegate?.takeOverSession) {
      notifyError(new Error('session delegate unavailable'), copy.errorTakeOverFailed); return
    }
    triggerHaptic('submit'); setPending(true)
    try {
      const result = await delegate.takeOverSession(sessionId)
      if (!result.acknowledged) notify({ kind: 'warning', message: copy.errorTakeOverUnconfirmed })
      if (storedSessionId) await delegate.resumeTile(storedSessionId, { refreshTranscript: true }).catch(() => undefined)
      aui.message().reload()
    } catch (error) { notifyError(error, copy.errorTakeOverFailed) } finally { setPending(false) }
  }, [aui, copy.errorTakeOverFailed, copy.errorTakeOverUnconfirmed, sessionId, storedSessionId])
  return <button className="aui-error-action" disabled={pending} onClick={() => void takeOver()} type="button">{label}</button>
}
// in ErrorRecoveryActions, before Start new session:
{plan.takeOver && <TakeOverAction label={copy.errorTakeOver} />}
```

`apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts` (0014):

```ts
takeOverSession: async runtimeId => {
  const storedSessionId = storedSessionIdForRuntime(runtimeId)
  return storedSessionId
    ? await requestForStoredSession<SessionTakeOverResult>(storedSessionId, 'session.take_over', { session_id: runtimeId })
    : await requestGateway<SessionTakeOverResult>('session.take_over', { session_id: runtimeId })
},
```

`apps/desktop/src/lib/error-surface.ts` (0014): `SESSION_NOT_OWNED: { retry: false, startNewSession: true, takeOver: true }`,
and the default plan gets `takeOver: false`. In `status.ts`, `gatewayErrorSurface(payload, msg)` returns
`parseErrorSurface(payload.error_surface)` when present. Otherwise it falls back to the old text sniffing
(`isSessionNotOwnedError` and disk-full detection).

`apps/desktop/src/app/chat/right-rail/preview-active-tab.ts` (0015):

```ts
export function backgroundPreviewTabFor(owner: PreviewOwner | undefined): null | PreviewTab {
  if (owner === undefined || isBrowserWindow()) return null
  const own = previewTabsFor(owner).filter(tab => !tab.pinned)
  if (own.length === 0) return null
  const onScreen = new Set($visiblePreviewTabs.get().map(tab => tab.id))
  if (own.some(tab => onScreen.has(tab.id))) return null
  const remembered = rememberedPreviewTabId(ownerIdentity(owner).sessionId)
  return own.find(t => t.id === remembered) ?? own.find(t => t.target.kind === 'url') ?? own[0] ?? null
}
export function activePreviewTabFor(owner?: PreviewOwner): null | PreviewTab {
  return backgroundPreviewTabFor(owner) ?? resolveActivePreviewTab(previewTabsFor(owner))
}
```

`.../gateway-event/server-requests.ts` (0015):

```ts
const BACKGROUND_PREVIEW_REQUESTS = new Set(['preview.act', 'preview.read'])
export function windowHoldsBackgroundPreview(sessionId: string): boolean {
  if (!sessionId) return false
  const owner = previewOwnerFor(sessionId)
  return Boolean(backgroundPreviewTabFor(owner)) && hasLivePreviewSurface(owner)
}
// handleServerRequest, inside `if (route === 'ignore') {`:
if (BACKGROUND_PREVIEW_REQUESTS.has(request.method) && windowHoldsBackgroundPreview(sessionId)) {
  handler({ deps, request, sessionId, isActiveSession: false })
  return true
}
// previewAct: compute `owner` first, then refuse only if
if (!isActiveSession && !backgroundPreviewTabFor(owner)) { answerValue(request, { error: '...', success: false }); return }
```

i18n (0014) adds the keys `assistant.thread.errorTakeOver`, `errorTakeOverUnconfirmed` and `errorTakeOverFailed`:

- `types.ts`: type declarations.
- Translations in `en`, `de`, `es`, `fr`, `ja`, `zh`, `ar_assistant`, `zh-hant_assistant`.
- `ru` is listed under each key in `overlay-gaps.json`.
- `locales/_keys.desktop.json` lists all three.
- The `SESSION_NOT_OWNED` card body is reworded in en/de/es/fr.

## Tests

Python. Use the repo runner (AGENTS.md: never bare `pytest`):

```bash
scripts/run_tests.sh tests/hermes_cli/test_active_session_takeover.py \
  tests/tui_gateway/test_session_takeover.py tests/tui_gateway/test_session_takeover_e2e.py \
  tests/tui_gateway/contracts/test_generated.py
```

- `test_active_session_takeover.py`: registry level. It uses a real live foreign pid (`sleep 60`). Covers the
  cap/re-entrancy/fail-closed cases, ack, dead-pid-counts-as-ack, a torn registry, and a real second Python process.
- `test_session_takeover.py`: drives `_takeover_watch_pass()` directly for an idle session, a running turn and a
  pending clarify. Also covers the RPC (ack, timeout, history reload, already_owner, unreadable registry, unknown
  session, `_LONG_HANDLERS`, same-process sibling).
- `test_session_takeover_e2e.py`: two real gateway processes sharing one `HERMES_HOME`. No model calls.
- `contracts/test_generated.py` (stock): fails if the generated TS/OpenRPC files are stale.

Desktop, from `apps/desktop` (vitest):

```bash
npx vitest run src/lib/error-surface.test.ts \
  src/components/assistant-ui/thread/assistant-message.test.tsx \
  src/app/session/hooks/use-message-stream/gateway-event/status.test.ts \
  src/app/session/hooks/use-message-stream/gateway-event/server-requests.test.ts \
  src/i18n/overlay-completeness.test.ts
node scripts/i18n-keys.mjs --check
```

Verified on upstream/main (`e06df9c2ea`) with 0001–0015 applied and 0013 resolved as described below:

- Python: 28 tests passed.
- vitest: 5 files, 123 tests passed.
- `gen_gateway_contracts.py --check` and `i18n-keys.mjs --check` were both clean.

## Applying

```bash
cd ~/.hermes/hermes-agent        # on upstream/main, after 0001–0012
git am -3 ~/code/hermes-mods/core/patches/0013-feat-sessions-take-over-a-chat-open-in-another-Herme.patch
# -> CONFLICT in tui_gateway/server.py (see below); resolve, git add, git am --continue
git am -3 ~/code/hermes-mods/core/patches/0014-feat-desktop-Take-over-here-on-a-chat-open-in-anothe.patch
git am -3 ~/code/hermes-mods/core/patches/0015-feat-desktop-a-hidden-chat-s-agent-keeps-driving-its.patch
```

**Resolving the 0013 conflict.** It is a single hunk in the `_LONG_HANDLERS` frozenset near line 192 of
`tui_gateway/server.py`. Upstream deleted the `onboarding.ensure_setup_*`/`onboarding.reset_setup_profile` and
`session.start_chat` RPCs (commit "refactor: delete the onboarding and start_chat RPCs…") and now lists
`"onboarding.start", "onboarding.state",`. The patch's context still has the old names. Resolve it like this:

- Keep upstream's line.
- Add only `session.take_over`.
- Do **not** re-add `session.start_chat` or the `onboarding.ensure_*` names. Those methods no longer exist, and a
  registered name without a contract fails `test_every_method_has_a_contract`.

```python
    "session.resume", "session.save", "session.workspace.move", "shell.exec", "skills.manage", "slash.exec",
    "onboarding.start", "onboarding.state",
    "session.take_over",  # take_over waits for the old owner's ack
    "command.dispatch",  # /goal draft invokes the auxiliary model; never block the RPC reader
```

Every other file in 0013 merges cleanly. All the helpers it calls already exist upstream:

| Helper                                                                                                      | Module                                  |
|-------------------------------------------------------------------------------------------------------------|-----------------------------------------|
| `_take_over_detached_runtime_lease`, `_attach_lease`, `_lease_metadata`, `_interrupt_session_turn`, `_SESSION_OWNERSHIP_UNAVAILABLE` | `tui_gateway/session_lifecycle.py`      |
| `_load_durable_truncation_history`                                                                          | `tui_gateway/methods_prompt.py`         |
| `_session_source`                                                                                           | `tui_gateway/session_workdir.py`        |
| `server_requests.cancel`                                                                                    | `tui_gateway/server_requests.py`        |
| `spawn_context_thread`                                                                                      | `agent/memory_provider.py`              |
| `env_float`                                                                                                 | `tui_gateway/_env.py`                   |

With that resolution, 0014–0030 still apply in order. 0020 later appends `"session.title.suggest"` to the same
frozenset; `-3` merges it fine.

Dependencies:

- 0014 needs 0013. It imports `SessionTakeOverResult` from `@hermes/shared` and calls `session.take_over`, so it
  applies alone but fails typecheck and the runtime without 0013.
- 0015 is independent: desktop only, applies with `-3` on bare upstream.
- None of the three depends on 0001–0012. They were only tested in sequence after them.

After applying, rebuild the desktop app (0014/0015) and restart the backend (0013). Every backend that might be
displaced needs 0013 too, because the old owner's watcher is what stops its turn and acks. See Gotchas.

## Gotchas

- **Generated RPC contract.** `apps/shared/src/gateway-contract.generated.ts` and
  `apps/shared/src/gateway-contract.openrpc.json` are rendered from the Pydantic models in `tui_gateway/contracts/`
  by `scripts/gen_gateway_contracts.py`.
  - It is Python-only and runs Prettier on the TS when node_modules exist.
  - Regenerate with `scripts/gen_gateway_contracts.py`; verify with `--check`.
  - The script's shebang goes through `scripts/run-in-hermes-env`, which syncs the repo's uv env. An older venv may
    lack newer deps (e.g. `ruamel`) and fail on import. In that case run `bash scripts/run-in-hermes-env python3
    scripts/gen_gateway_contracts.py`.
  - If you re-implement against a newer upstream, edit the models and regenerate. Don't hand-merge the generated
    files. `tests/tui_gateway/contracts/test_generated.py` fails when they are stale.
- **The old owner must run 0013 as well.** Against an unpatched owner the takeover still succeeds: the registry entry
  is replaced and the new window gets the lease. But nothing stops the old process's turn. After 8 s the RPC returns
  `acknowledged: false` and the warning toast appears. Until it next tries to claim the lease, the old process can
  keep writing to the same session row.
- **The registry fails closed.** An unreadable `runtime/active_sessions.json` refuses with 4090
  `SESSION_COORDINATION_UNAVAILABLE` and is never overwritten. Fix or delete the file by hand.
- **Profiles.** The lease lives in the owning profile's registry (`session["profile_home"]`). The desktop delegate
  routes through `requestForStoredSession` for that reason. Don't call `requestGateway` directly for stored sessions.
- **Watcher in tests.** It does not start when `HERMES_TEST_ISOLATION` is set, which the test harness sets. Tests
  call `_takeover_watch_pass()` directly. The e2e test pops the variable for process A on purpose.
- **i18n.** Every new desktop key must be:
  - typed in `apps/desktop/src/i18n/types.ts`
  - present in `en.ts`
  - present in each locale overlay, or listed for that locale in `overlay-gaps.json` (`overlay-completeness.test.ts`
    ratchets)
  - present in `locales/_keys.desktop.json` (regenerate with `npm run i18n:keys` in `apps/desktop`; the build never
    writes it)
- **Rebuild the desktop app** after 0014/0015: `hermes desktop --build-only --force-build`. On macOS, set
  `APPLE_SIGNING_IDENTITY` or the app is ad-hoc signed.
- **0015 depends on parked panes.** It relies on stock pane-lifecycle "parked" panes, which keep the webview mounted
  while off screen. If upstream ever unmounts hidden Browser tabs, `hasLivePreviewSurface` returns false and requests
  are declined again (safe, but the feature goes away).

## Could this be a plugin instead?

Mostly no.

- **0013** has to be in core. It changes the lease protocol in `hermes_cli/active_sessions.py`: a new registry write
  under the lease lock, plus `took_over_from`/`handoff_acked` metadata. It also needs a watcher bound to `server.py`
  internals (`_sessions_lock`, `_interrupt_session_turn`, `server_requests`), and every gateway must be in on the
  protocol. `register_method` is an internal seam, and a registered method needs a contract model or
  `test_every_method_has_a_contract` fails. A Python plugin could in principle monkeypatch all of this in, but it
  would be a fork in disguise.
- **0014** could in principle be a palette/keybind command in a desktop plugin that calls `session.take_over` and then
  resends. But the plugin SDK has no contribution point on the in-thread error card, and no access to
  `aui.message().reload()`, so the one-click recovery needs core.
- **0015** changes how core routes server requests (`handleServerRequest`, `previewAct`). The plugin SDK has no hook
  there.
