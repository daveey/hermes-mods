# ⌘⇧E (formerly ⌘⇧N): rename a session with an AI-suggested short name

Press **⌘⇧E** (`mod+shift+e`; ⌘⇧N until patch 0042, which gave ⌘⇧N to "new chat on the current gateway") in Hermes Desktop to open the "Rename session" dialog for the selected sidebar
session, or the focused chat if nothing is selected. While the dialog is open, the auxiliary title model reads the
session's transcript. It then pre-fills the input with a short name of 2–4 lowercase words, such as
`hermes shortcuts` or `vast gpu cleanup`, and selects the text. Press Enter to accept it, or type over it.

Patch: [`patches/0020-feat-desktop-N-renames-the-session-pre-filled-with-a.patch`](patches/0020-feat-desktop-N-renames-the-session-pre-filled-with-a.patch)
(22 files, +325/−6).

## Behavior

- **Target:** the selected sidebar session (`$selectedStoredSessionId`). If none is selected, the focused chat
  (`$focusedStoredSessionId`) is used. If there is neither, the handler returns `false` ("not handled") and nothing
  opens.
- **Dialog:** the existing `RenameSessionDialog`, opened with the current title. While the request is in flight,
  it shows "Suggesting a name…" under the input (`sidebar.row.suggestingName`).
- **Pre-fill:** a non-empty suggestion replaces the input value, and the text is then selected. If you have already
  typed anything, a late suggestion is dropped and never overwrites your text.
- **Failure:** if the RPC errors, times out (45 s), or returns `title: null`, the dialog keeps the current title
  and works as a plain rename. The failure is only logged with `console.warn`.
- **Saving** uses the existing rename path (`session.title` RPC). The suggest RPC is read-only.
- **Row menu → Rename** is unchanged and does **not** ask for a suggestion.
- **Keybinds:** the new action `session.rename` (category `session`) defaults to `mod+shift+n`.
  **Upstream's `session.newWindow` loses its `mod+shift+n` default and ships unbound.** You can rebind both in
  Settings → Keybinds.
- **Model:** the suggestion uses the auxiliary task `title_generation`, the same model that auto-titling uses. There
  are no new config keys.
- **Name shape:** the result is lowercased and cut to at most 5 words. The prompt asks for 2–4 words.

## How it works

```
⌘⇧N ─► use-keybinds.ts 'session.rename' ─► openSessionRename({sessionId, profile})
        └─ $sessionRenameTarget (store/session-rename.ts)
             └─ <GlobalSessionRenameDialog/> (mounted once in sidebar/index.tsx)
                  └─ <RenameSessionDialog suggest/> ─► gateway.request('session.title.suggest', …, 45_000)
                        tui_gateway/methods_session.py ─► db.get_messages(key)
                          └─ agent/title_generator.py suggest_short_name() ─► call_llm(task="title_generation")
                  Enter ─► existing 'session.title' RPC (unchanged)
```

- **`agent/title_generator.py`** adds `build_short_name_input(messages)` and `suggest_short_name(messages)`.
  - The excerpt keeps only user and assistant text. It reuses the module's existing `flatten_message_text` and
    `strip_control_wrappers`.
  - Each message is clipped to 600 chars. A transcript longer than 10 turns keeps the first 4 and last 6, joined
    by `…`, because a session's subject often drifts. The whole excerpt is capped at 6000 chars.
  - The model call reuses the existing `_TITLE_RESPONSE_FORMAT` (JSON `{"title": …}`), `TITLE_MAX_TOKENS`,
    `_extract_title_text`, `_title_from_reasoning` and `_clean_title` helpers, so it parses exactly the way
    auto-titling does. Reasoning is off.
  - It fails open: an empty excerpt or any exception returns `None`.
- **`tui_gateway/methods_session.py`** adds the new RPC `session.title.suggest`.
  - It resolves the session id against the profile DB (`_profile_db(params)`, which honors `profile`), loads
    `db.get_messages`, and returns `{session_key, title}`.
  - Errors: `4006` when no id is given, `5007` when the DB is unavailable or the lookup fails.
- **`tui_gateway/server.py`** adds `session.title.suggest` to `_LONG_HANDLERS`. The aux-model call then runs on
  the RPC worker pool instead of blocking the reader.
- **`apps/desktop/src/store/session-rename.ts`** (new) holds the nanostore atom that drives the global dialog.
- **`apps/desktop/src/app/chat/sidebar/session-actions-menu.tsx`**:
  - `RenameSessionDialog` is now exported and gains a `suggest` prop.
  - The new `GlobalSessionRenameDialog` exists because each row's dialog only exists while that row's menu is
    mounted, and a keybind can fire with no menu open.
  - The global dialog is keyed by `sessionId` and unmounts when it closes, so each open starts with a fresh
    `touchedRef` and a fresh request.
- **`apps/desktop/src/lib/keybinds/actions.ts`** and **`apps/desktop/src/app/hooks/use-keybinds.ts`** register the
  action and add its handler.

## Code

### agent/title_generator.py (appended at end of file)

```python
_SHORT_NAME_PROMPT = (
    "You name chat sessions so the user can find them again in a sidebar. Read the conversation and "
    "reply with ONLY a short, recognizable name of 2-4 lowercase words naming its subject (e.g. "
    "\"hermes shortcuts\", \"vast gpu cleanup\", \"tax return review\"). No quotes, no punctuation, "
    "no explanation. Prefer concrete nouns (project, tool, topic) over verbs. "
    'Return JSON: {"title": "..."}'
)
_SHORT_NAME_MAX_CHARS = 6000
_SHORT_NAME_PER_MESSAGE = 600


def build_short_name_input(messages: list) -> str:
    turns = []
    for msg in messages or []:
        role = (msg.get("role") if isinstance(msg, dict) else None) or ""
        if role not in ("user", "assistant"):
            continue
        try:
            text = flatten_message_text(msg.get("content"))
        except Exception:
            text = msg.get("content") if isinstance(msg.get("content"), str) else ""
        text = " ".join(strip_control_wrappers(text or "").split())
        if text:
            turns.append(f"{role}: {text[:_SHORT_NAME_PER_MESSAGE]}")
    picked = turns if len(turns) <= 10 else turns[:4] + ["…"] + turns[-6:]
    return "\n".join(picked)[:_SHORT_NAME_MAX_CHARS]


def suggest_short_name(messages: list, *, timeout: float = 30.0, llm: Callable[..., Any] | None = None) -> Optional[str]:
    excerpt = build_short_name_input(messages)
    if not excerpt.strip():
        return None
    try:
        response = (llm or call_llm)(
            task="title_generation",
            messages=[{"role": "system", "content": _SHORT_NAME_PROMPT}, {"role": "user", "content": excerpt}],
            max_tokens=TITLE_MAX_TOKENS, temperature=None, timeout=timeout,
            extra_body={"response_format": _TITLE_RESPONSE_FORMAT}, reasoning_config={"enabled": False},
        )
        message = response.choices[0].message
        title = _clean_title(_extract_title_text(message.content or "") or _title_from_reasoning(message))
    except Exception:
        logger.debug("Short-name suggestion failed", exc_info=True)
        return None
    if not title:
        return None
    words = title.lower().split()
    return " ".join(words[:5]) or None
```

### tui_gateway/methods_session.py (new RPC, placed before `session.archive`)

```python
@method("session.title.suggest")
def _(rid, params: dict) -> dict:
    """Read-only; ``title`` is None when nothing could be suggested."""
    from agent.title_generator import suggest_short_name
    target = str(params.get("session_key") or params.get("session_id") or "")
    if not target:
        return _err(rid, 4006, "session_id required")
    live = _sessions.get(str(params.get("session_id") or ""))
    with (_profile_db(params) if live is None else _session_db(live)) as db:
        if db is None:
            return _db_unavailable_error(rid, code=5007)
        try:
            if live is not None:
                key = live["session_key"]
            else:
                key = (db.resolve_session_id(target) if hasattr(db, "resolve_session_id") else None) or target
            messages = db.get_messages(key)
        except Exception as e:
            return _err(rid, 5007, str(e))
    return _ok(rid, {"session_key": key, "title": suggest_short_name(messages)})
```

### tui_gateway/server.py

```python
_LONG_HANDLERS = frozenset({
    ...
    "session.title.suggest",  # aux-model call (⌘⇧N rename pre-fill)
})
```

### apps/desktop/src/store/session-rename.ts (new)

```ts
import { atom } from 'nanostores'

/** Session the global rename dialog (⌘⇧N) edits; null = closed. */
export interface SessionRenameTarget {
  profile?: string
  sessionId: string
}

export const $sessionRenameTarget = atom<SessionRenameTarget | null>(null)

export function openSessionRename(target: SessionRenameTarget): void {
  $sessionRenameTarget.set(target)
}

export function closeSessionRename(): void {
  $sessionRenameTarget.set(null)
}
```

### apps/desktop/src/lib/keybinds/actions.ts

```ts
// Fork: ⌘⇧N belongs to `session.rename`; new window ships unbound (rebindable).
{ id: 'session.newWindow', category: 'session', defaults: [] },
// ...
{ id: 'session.togglePin', category: 'session', defaults: [] },
{ id: 'session.rename', category: 'session', defaults: ['mod+shift+n'] },
```

### apps/desktop/src/app/hooks/use-keybinds.ts (handler map)

```ts
import { openSessionRename } from '@/store/session-rename'
// ...
'session.rename': () => {
  const sessionId = $selectedStoredSessionId.get() || $focusedStoredSessionId.get()

  if (!sessionId) {
    return false
  }

  const profile = $sessions.get().find(s => sessionMatchesStoredId(s, sessionId))?.profile || undefined
  openSessionRename({ profile, sessionId })
},
```

### apps/desktop/src/app/chat/sidebar/session-actions-menu.tsx

```tsx
import { $sessionRenameTarget, closeSessionRename } from '@/store/session-rename'

interface RenameSessionDialogProps {
  // ...existing: open, onOpenChange, sessionId, currentTitle, profile
  /** Pre-fill with an AI-suggested short name (`session.title.suggest`) — ⌘⇧N. */
  suggest?: boolean
}

export function RenameSessionDialog({ open, onOpenChange, sessionId, currentTitle, profile, suggest = false }: RenameSessionDialogProps) {
  // ...existing value/submitting state and inputRef
  const [suggesting, setSuggesting] = useState(false)
  // Once the user types, a late suggestion must not clobber their edit.
  // Fresh per mount: the ⌘⇧N host unmounts the dialog on close.
  const touchedRef = useRef(false)

  useEffect(() => {
    const gateway = activeGateway()

    if (!open || !suggest || !sessionId || !gateway) {
      return
    }

    let cancelled = false
    const targetProfile =
      (profile ?? '').trim() || $sessions.get().find(s => sessionMatchesStoredId(s, sessionId))?.profile || undefined

    setSuggesting(true)
    gateway
      .request<{ title?: null | string }>(
        'session.title.suggest',
        { session_key: sessionId, ...(targetProfile ? { profile: targetProfile } : {}) },
        45_000
      )
      .then(result => {
        const title = result?.title?.trim()

        if (!cancelled && title && !touchedRef.current) {
          setValue(title)
          window.setTimeout(() => inputRef.current?.select(), 0)
        }
      })
      .catch(err => console.warn('session.title.suggest failed', err))
      .finally(() => {
        if (!cancelled) {
          setSuggesting(false)
        }
      })

    return () => {
      cancelled = true
      setSuggesting(false)
    }
  }, [open, profile, sessionId, suggest])

  // <Input onChange={event => { touchedRef.current = true; setValue(event.target.value) }} … />
  // {suggesting && <p className="text-xs text-muted-foreground">{r.suggestingName}</p>}   (above DialogFooter)
}

// One app-wide instance driven by `$sessionRenameTarget` (per-row dialogs only
// exist while their row's menu is mounted).
export function GlobalSessionRenameDialog() {
  const target = useStore($sessionRenameTarget)
  const sessions = useStore($sessions)

  if (!target) {
    return null
  }

  const session = sessions.find(s => sessionMatchesStoredId(s, target.sessionId))

  return (
    <RenameSessionDialog
      currentTitle={session?.title ?? ''}
      key={target.sessionId}
      onOpenChange={open => {
        if (!open) {
          closeSessionRename()
        }
      }}
      open
      profile={target.profile}
      sessionId={target.sessionId}
      suggest
    />
  )
}
```

`apps/desktop/src/app/chat/sidebar/index.tsx` imports `GlobalSessionRenameDialog` and mounts it once, right after
`<ProjectDialog />`.

### i18n

| Key | Added to |
|---|---|
| `keybinds.actions['session.rename']` ("Rename current session…") | en, de, es, fr, ru, zh, ar_chrome |
| `sidebar.row.suggestingName` ("Suggesting a name…") | en, de, es, fr, ru, zh, ja, ar_chrome, zh-hant_chrome; typed in `i18n/types.ts` (`Translations.sidebar.row`) |
| `overlay-gaps.json` | `"keybinds.actions.session.rename": ["ja","zh-hant"]` |
| `locales/_keys.desktop.json` | `keybinds.actions.session.rename` and `sidebar.row.suggestingName`, plus `keybinds.actions.session.moveToProject` and `sidebar.projects.movePlaceholder`, which 0018 left out |

## Tests

- **`tests/agent/test_short_name_suggest.py`** (new) covers:
  - the excerpt filter (user and assistant only), and head 4 / `…` / tail for 20 turns;
  - lowercasing and JSON parsing, with `task == "title_generation"`;
  - failing open on an exception and on an empty transcript.

  The model is injected through the `llm=` kwarg, so no network calls are made.
- **`apps/desktop/src/app/chat/sidebar/session-actions-menu.test.tsx`** gets 3 new cases. Its i18n mock adds
  `suggestingName`, and the tests mock `activeGateway`. The cases cover:
  - the pre-fill and the exact RPC call (`'session.title.suggest', {profile, session_key}, 45_000`);
  - a late suggestion not overwriting typed text;
  - no RPC call without `suggest`.

```sh
# from the hermes-agent repo root, with the repo's venv active
pytest tests/agent/test_short_name_suggest.py
pytest tests/tui_gateway/contracts/test_generated.py   # fails until a contract is added — see Gotchas

cd apps/desktop
npx vitest run src/app/chat/sidebar/session-actions-menu.test.tsx   # 13 passed on the fork
npx vitest run src/i18n/overlay-completeness.test.ts
```

## Applying

On current `upstream/main`, `git am -3` of 0020 **alone fails**. The patches it textually needs are:

- **0002**: the `navCollapsed` code in `sidebar/index.tsx` that 0020 reformats.
- **0004**, a prerequisite of 0018 (`store/projects.ts`).
- **0018** (⌘⇧P move-to-project): the `session.moveToProject` lines used as context in `use-keybinds.ts`, the
  locale files and `overlay-gaps.json`.

0019 (⌘⇧A archive) is not required. Verified on a scratch clone of upstream/main: `0002 0004 0018 0020` and
`0002 0004 0018 0019 0020` both apply cleanly. The full series 0001–0020 also applies in order, except 0013.

```sh
cd ~/.hermes/hermes-agent   # your hermes-agent checkout
for n in 0002 0004 0018 0020; do
  git am -3 /path/to/hermes-mods/core/patches/$n-*.patch || break
done
# or the whole series (0013 conflicts in tui_gateway/server.py: resolve it, or `git am --skip`):
git am -3 /path/to/hermes-mods/core/patches/00*.patch
```

Later patches that add neighbouring keybind actions (0021, 0022, 0026) expect 0020's lines as context. Apply them
after 0020.

## Gotchas

- **The RPC contract is in a follow-up patch, 0032.** 0020 alone adds `session.title.suggest`
  without a contract in `tui_gateway/contracts/`, so
  `tests/tui_gateway/contracts/test_generated.py::test_catalog_covers_the_whole_wire` fails with
  "methods without a contract". At runtime nothing breaks, because `rpc_dispatch.py` skips validation for a
  method without a contract. Patch 0032 adds `SessionTitleSuggestParams`/`SessionTitleSuggestResult` and
  `method("session.title.suggest", …)` in `tui_gateway/contracts/sessions.py`, and regenerates
  `apps/shared/src/gateway-contract.generated.ts` and `openrpc.json` with
  `bash scripts/run-in-hermes-env python3 scripts/gen_gateway_contracts.py`. Apply 0032 with 0020.
  0034 regenerates `locales/_keys.desktop.json` for the new labels.
- **Live-session lookup only checks `session_id`.** The desktop sends only `session_key`, so the RPC always takes
  the DB path (`_profile_db(params)` + `resolve_session_id`). That is fine for persisted sessions. A brand-new chat
  with no stored messages gets `title: null`, and the dialog falls back to a plain rename.
- **⌘⇧N steals upstream's new-window chord.** Shipping the same default on two actions shows a permanent conflict
  in the keybinds panel (see the comment above `view.findNext` in `actions.ts`), which is why `session.newWindow`
  was emptied. If upstream adds its own `session.rename` or reassigns ⌘⇧N, reconcile the defaults.
  `electron/main.ts` registers no accelerator for New Window ("⌘⇧N is a rebindable renderer keybind"), so the
  menu needs no change.
- **i18n has three places to keep in sync:**
  - every overlay locale must translate each new key, or list it in `src/i18n/overlay-gaps.json`
    (`overlay-completeness.test.ts` ratchets on this);
  - `locales/_keys.desktop.json` must match `en.ts`. Regenerate it with `npm run i18n:keys` in `apps/desktop`;
    `scripts/i18n-keys.test.mjs` pins it;
  - `types.ts` must declare `suggestingName`, or `tsc` (`npm run typecheck`) fails.
- **Unrelated churn in `sidebar/index.tsx`.** The patch also adds blank lines and reorders JSX props there. If you
  get conflicts in that churn, it is safe to resolve either way. Only the import and the
  `<GlobalSessionRenameDialog />` mount matter.
- **The desktop app must be rebuilt** to pick up the change: `hermes desktop --build-only --force-build`. On macOS,
  set `APPLE_SIGNING_IDENTITY`, or the app is ad-hoc signed. The Python side takes effect when the gateway or
  backend restarts.
- **Cost:** every ⌘⇧N press makes one aux-model call (up to about 6 KB of transcript). The row-menu rename makes
  none.

## Could this be a plugin instead?

**Partly. The UI could be a plugin, but the name generation can't.**

- **The desktop half could be a plugin** using `@hermes/plugin-sdk`:
  - `KEYBINDS_AREA` and `PALETTE_AREA` contributions, as in `desktop-plugins/spin-out/plugin.js`;
  - `host.state.focusedStoredSessionId` to pick the target;
  - a plugin-rendered dialog;
  - `host.request('session.title', …)` to save.

  Limits: `host.state` exposes the focused session but not the sidebar *selection*. A plugin also cannot clear the
  core `session.newWindow` default, so it would need a different chord or a manual rebind.
- **The backend half needs core.** Nothing in the desktop SDK registers a new tui_gateway RPC, and no existing RPC
  returns an aux-model title for an arbitrary stored session. Auto-titling (`maybe_auto_title`) only runs on the
  live turn path.

A plugin version therefore still needs the small core RPC (`suggest_short_name` + `session.title.suggest` + a
contract). The clean split for upstreaming is the backend RPC alone, with the ⌘⇧N UI shipped as a desktop plugin.
