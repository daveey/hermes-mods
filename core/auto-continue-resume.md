# Auto-continue: resume interrupted turns after a backend restart

When the Hermes backend restarts (app relaunch, `hermes update`, `hermes-sync` restart, backend respawn) while
Desktop chats are mid-turn, those turns pick up again on their own. You don't have to re-open each chat and send
"continue". Two patches do this:

- `patches/0008-feat-desktop-desktop.auto_continue.resume_on_startup.patch`: when
  `desktop.auto_continue.resume_on_startup` is on, the backend resumes every interrupted session at boot.
- `patches/0010-fix-desktop-a-turn-stopped-by-backend-shutdown-keeps.patch`: a turn stopped by backend
  shutdown keeps its crash marker, so auto-continue resumes it.

## Background: how stock auto-continue works

- When a Desktop turn starts, the backend writes a **turn marker** (`tui_gateway/turn_marker.py`) to
  `<HERMES_HOME>/desktop/interrupted_turns.json`. Each entry holds the prompt, `started_at`, `attempts`,
  `auto_continue` and `writer_pid` (+ `writer_start_time`).
- The marker is retired (`_retire_turn_marker` in `tui_gateway/session_auto_continue.py`) just before the turn's
  terminal frame. Callers: `tui_gateway/prompt_turn.py` (normal end) and `tui_gateway/methods_session_interrupt.py`
  (user Stop). If the process dies mid-turn, the marker stays behind.
- On `session.resume` (the user re-opens the chat), `_maybe_schedule_auto_continue` finds a leftover marker and
  re-runs the turn if all of these hold:
  - the marker is within `freshness_minutes`
  - it is under `max_attempts` (the crash-loop breaker)
  - its writer process is gone (`marker_writer_state(entry) != "alive"`, pid + start time)
  - the session isn't a hosted room

Stock Hermes has two gaps:

1. **The continuation only fires when the user opens each session.** After a restart, every chat that was
   mid-turn sits idle until you visit it.
2. **A graceful shutdown looks like a user Stop.** On SIGTERM or atexit, `_stop_turns_before_exit`
   (`tui_gateway/session_reaper.py`) interrupts running turns cleanly. The interrupted turn then reaches its
   terminal frame, and that retires the marker. So a restart left nothing to auto-continue, and the chat stayed on
   "Operation interrupted: waiting for model response". Only a hard crash (SIGKILL, power loss) left a marker.

## Behavior

Config (`~/.hermes/config.yaml`, or a profile's `config.yaml`):

```yaml
desktop:
  auto_continue:
    enabled: true             # stock, default true; must be on too
    freshness_minutes: 15     # stock, default 15; markers older than this are not resumed
    max_attempts: 2           # stock, default 2; crash-loop breaker per turn
    resume_on_startup: false  # new (0008), default false; resume all interrupted sessions at backend boot
    # resume_on_startup_delay_seconds: 5   # new, optional; read by the sweep, NOT in DEFAULT_CONFIG
```

| Key | Default | Patch | Effect |
|---|---|---|---|
| `desktop.auto_continue.resume_on_startup` | `false` | 0008 | At boot, resume every fresh interrupted session |
| `desktop.auto_continue.resume_on_startup_delay_seconds` | `5` | 0008 | Settle delay before the sweep runs |
| `desktop.auto_continue.freshness_minutes` | `15` | stock | Also used by the sweep's pre-filter |
| `desktop.auto_continue.max_attempts` | `2` | stock | Also used by the sweep's pre-filter |
| `desktop.auto_continue.enabled` | `true` | stock | `false` disables the sweep even if `resume_on_startup` is true |

Truthiness: `resume_on_startup` accepts `true/"true"/"True"/1/"1"/"on"/"yes"`. `enabled` is off only for
`false/"false"/"False"/0/"0"/"off"/"no"`. Bad numbers fall back to the defaults; negatives clamp to 0.

What the user sees:

- **With 0010 only:** after a graceful restart, opening a chat that was mid-turn auto-continues it (as stock
  already did after a hard crash).
- **With 0008 + `resume_on_startup: true`:** ~5 s after the backend boots, every chat that was mid-turn (in the
  launch home and every profile) resumes in the background. Opening one later attaches to the live session and
  streams the running turn.
- **With 0008 only:** the sweep only finds markers from hard crashes (graceful restarts retired them).
- A user Stop still retires the marker: stopped turns never come back. Idle sessions are never flagged.
- Turns are skipped when: stale, at the attempt cap, `auto_continue: false` on the marker, empty prompt, or the
  writer process is still alive (a sibling backend owns the turn).
- Order: newest `started_at` first. One failing session doesn't stop the rest.
- Logs (logger `tui_gateway.startup_resume`): per session `startup resume <key> (profile=<name|default>):
  auto-continue scheduled` / the error / `resumed, no continuation scheduled`, then `startup resume: N
  interrupted session(s) processed`. If the sweep can't start: warning `startup resume sweep did not start` from
  `hermes_cli/web_server.py`.

## How it works

Backend-only (Python). No desktop React changes, no new JSON-RPC methods, no contract change.

0008 – startup sweep:

1. `hermes_cli/web_server.py` `_lifespan` (the FastAPI lifespan of the backend the Desktop spawns; also
   `hermes dashboard`) calls `tui_gateway.startup_resume.start_startup_resume_sweep()` right after the
   hosted-room startup thread, inside `try/except` → warning on failure.
2. `start_startup_resume_sweep()` loads config (`hermes_cli.config.load_config`), returns `None` unless
   `enabled and resume_on_startup`. Otherwise starts daemon thread `startup-resume-sweep`.
3. The thread sleeps `resume_on_startup_delay_seconds`, then fetches the in-process handler
   `tui_gateway.server._methods["session.resume"]` and calls `resume_interrupted_sessions(handler, home, cfg)`.
4. `_homes(home)` = launch `HERMES_HOME` + each `profiles/<name>/` that has a marker file.
5. `pending_resumes()` reads markers with `turn_marker._load` / `read_turn_marker` and pre-filters (freshness,
   attempts, `auto_continue`, writer liveness via `marker_writer_state`). Empty prompts are dropped because
   `read_turn_marker` returns `None` for them.
6. For each row: `handler(f"startup-resume-{n}", {"session_id": key, "omit_messages": True[, "profile": name]})`.
   This is the same path as the Desktop opening the chat, so the stock guards in `_maybe_schedule_auto_continue`
   run again; the pre-filter only avoids resuming sessions that wouldn't continue anyway. The handler returns a
   JSON-RPC envelope; `result.auto_continue` truthy ⇒ scheduled.

0010 – shutdown keeps the marker:

1. `tui_gateway/session_reaper.py` `_stop_turns_before_exit` (run from `server.py`'s exit sequence after
   `_flush_sessions_before_exit` and `_release_gateway_wake_owner`) sets `session["_exit_keeps_turn_marker"] =
   True` under `history_lock` on each `running` session, before `_interrupt_session_turn`.
2. `_retire_turn_marker` returns early when the flag is set, so the interrupted turn's terminal frame leaves the
   marker. Next boot treats it like a crash.

## Code

### `tui_gateway/startup_resume.py` (new, 0008; docstring trimmed)

```python
from __future__ import annotations
import itertools, logging, threading, time
from pathlib import Path
from typing import Any, Callable
from tui_gateway.turn_marker import _load, _marker_path, marker_writer_state, read_turn_marker

logger = logging.getLogger(__name__)
_ids = itertools.count(1)

def _settings(cfg: dict | None) -> tuple[bool, float, int, float]:
    """(resume_on_startup, freshness seconds, max_attempts, startup delay seconds) from ``desktop.auto_continue``."""
    desktop = (cfg or {}).get("desktop")
    ac = desktop.get("auto_continue") if isinstance(desktop, dict) else None
    ac = ac if isinstance(ac, dict) else {}
    enabled = ac.get("enabled", True) not in (False, "false", "False", 0, "0", "off", "no")
    on_start = ac.get("resume_on_startup", False) in (True, "true", "True", 1, "1", "on", "yes")
    try:
        fresh = max(0.0, float(ac.get("freshness_minutes", 15))) * 60.0
    except (TypeError, ValueError):
        fresh = 15 * 60.0
    try:
        attempts = max(0, int(ac.get("max_attempts", 2)))
    except (TypeError, ValueError):
        attempts = 2
    try:
        delay = max(0.0, float(ac.get("resume_on_startup_delay_seconds", 5)))
    except (TypeError, ValueError):
        delay = 5.0
    return enabled and on_start, fresh, attempts, delay

def _homes(home: Path) -> list[tuple[str | None, Path]]:
    """The launch home plus every named profile under it: markers are stored per HERMES_HOME."""
    out: list[tuple[str | None, Path]] = [(None, home)]
    with_profiles = home / "profiles"
    if with_profiles.is_dir():
        for p in sorted(with_profiles.iterdir()):
            if p.is_dir() and _marker_path(p).exists():
                out.append((p.name, p))
    return out

def pending_resumes(home: Path, cfg: dict | None, *, now: float | None = None) -> list[dict[str, Any]]:
    _, fresh, max_attempts, _ = _settings(cfg)
    now = time.time() if now is None else now
    out = []
    for profile, h in _homes(home):
        for key in list(_load(_marker_path(h))):
            m = read_turn_marker(h, key)
            if m is None or not m.get("auto_continue", True):
                continue
            if now - m["started_at"] > fresh or m["attempts"] >= max_attempts:
                continue
            if marker_writer_state(m) == "alive":
                continue  # a live sibling backend owns this turn
            out.append({"profile": profile, "home": str(h), "session_key": key, "started_at": m["started_at"]})
    out.sort(key=lambda r: r["started_at"], reverse=True)
    return out

def resume_interrupted_sessions(resume: Callable[[str, dict], Any], home: Path, cfg: dict | None) -> list[dict]:
    results = []
    for row in pending_resumes(home, cfg):
        params: dict[str, Any] = {"session_id": row["session_key"], "omit_messages": True}
        if row["profile"]:
            params["profile"] = row["profile"]
        try:
            env = resume(f"startup-resume-{next(_ids)}", params) or {}
        except Exception as exc:  # one bad session must not stop the rest
            logger.warning("startup resume of %s failed", row["session_key"], exc_info=True)
            results.append({**row, "ok": False, "error": str(exc)})
            continue
        err = env.get("error") if isinstance(env, dict) else None
        res = env.get("result") if isinstance(env, dict) else None
        scheduled = bool(isinstance(res, dict) and res.get("auto_continue"))
        logger.info("startup resume %s (profile=%s): %s", row["session_key"], row["profile"] or "default",
                    "auto-continue scheduled" if scheduled else (err or "resumed, no continuation scheduled"))
        results.append({**row, "ok": err is None, "scheduled": scheduled, **({"error": err} if err else {})})
    return results

def start_startup_resume_sweep() -> threading.Thread | None:
    try:
        from hermes_cli.config import load_config
        from hermes_constants import get_hermes_home
        cfg = load_config()
    except Exception:
        logger.debug("startup resume: config unavailable", exc_info=True)
        return None
    enabled, _, _, delay = _settings(cfg)
    if not enabled:
        return None

    def _run() -> None:
        time.sleep(delay)
        try:
            import tui_gateway.server as server
            handler = server._methods.get("session.resume")
            if handler is None:
                logger.warning("startup resume: session.resume handler not installed")
                return
            done = resume_interrupted_sessions(handler, Path(get_hermes_home()), cfg)
            if done:
                logger.info("startup resume: %d interrupted session(s) processed", len(done))
        except Exception:
            logger.exception("startup resume sweep failed")

    t = threading.Thread(target=_run, daemon=True, name="startup-resume-sweep")
    t.start()
    return t
```

### `hermes_cli/web_server.py` (0008, in `_lifespan` after `hosted_room_start_thread.start()`)

```python
    try:
        from tui_gateway.startup_resume import start_startup_resume_sweep

        start_startup_resume_sweep()
    except Exception:
        _log.warning("startup resume sweep did not start", exc_info=True)
```

### `hermes_cli/config_defaults.py` (0008, `DEFAULT_CONFIG["desktop"]["auto_continue"]`)

```python
            "freshness_minutes": 15,
            "max_attempts": 2,
            # Resume every interrupted session when the backend starts (app relaunch, `hermes update`),
            # not only the ones the user re-opens. Same freshness/attempt guards apply.
            "resume_on_startup": False,
```

### `tui_gateway/session_reaper.py` (0010, `_stop_turns_before_exit` loop)

```python
    for sid, session in running:
        # A backend exit is not a user Stop: keep the crash marker so auto-continue resumes this turn on the
        # next boot (the interrupted turn's terminal frame would otherwise retire it).
        with contextlib.suppress(Exception), session["history_lock"]:
            session["_exit_keeps_turn_marker"] = True
        with contextlib.suppress(Exception):
            _interrupt_session_turn(sid, session)
```

### `tui_gateway/session_auto_continue.py` (0010, top of `_retire_turn_marker`)

```python
def _retire_turn_marker(session: dict, *keys: str) -> None:
    """... A turn stopped by backend shutdown keeps its marker
    (``_stop_turns_before_exit`` flags it) so the next boot auto-continues it like a crash."""
    if session.get("_exit_keeps_turn_marker"):
        return
    ...
```

## Why both

0010 without 0008 is still useful: restarted turns now auto-continue when you open the chat. 0008 without 0010
only helps after hard crashes, because graceful restarts retired the markers. With both patches, any restart
resumes every turn that was mid-flight.

## Safety

- **Crash loops:** each continuation adds to `attempts`, and `max_attempts` (default 2) caps re-runs of one turn,
  including turns that keep getting killed by restarts.
- **Old turns:** `freshness_minutes` (default 15) keeps a backend that was down for hours from replaying old work.
- **Multiple backends:** if a marker's writer process is still alive, the sweep leaves it alone, so two Desktop
  backends never both run the same turn.
- **Finished turns:** the marker is retired before the terminal frame, not at thread end. Quitting during
  post-turn work therefore doesn't re-run a turn that already finished. 0010 changes this only for turns that
  were still `running` when shutdown began.

## Tests

- 0008 adds `tests/tui_gateway/test_startup_resume.py`; 0010 adds `tests/tui_gateway/test_exit_keeps_turn_marker.py`.
- Run from the repo root (the wrapper uses the repo venv):

```bash
scripts/run_tests.sh tests/tui_gateway/test_startup_resume.py tests/tui_gateway/test_exit_keeps_turn_marker.py
# or: venv/bin/python -m pytest tests/tui_gateway/test_startup_resume.py tests/tui_gateway/test_exit_keeps_turn_marker.py
```

What the tests cover:

- The sweep is off by default and stays off when `enabled: false`; `start_startup_resume_sweep()` returns `None`
  when disabled.
- The pre-filter keeps only the fresh marker and drops stale, capped, live-writer (`os.getpid()`), opted-out and
  empty-prompt ones.
- Profile homes are swept with the `profile` param, newest first.
- One failing resume (exception or error envelope) doesn't stop the rest.
- A turn stopped at shutdown keeps its marker. A user Stop and an idle session still retire theirs.

## Applying

From a hermes-agent checkout on current `upstream/main`:

```bash
git am -3 --empty=keep ~/code/hermes-mods/core/patches/0008-feat-desktop-desktop.auto_continue.resume_on_startup.patch
git am -3 --empty=keep ~/code/hermes-mods/core/patches/0010-fix-desktop-a-turn-stopped-by-backend-shutdown-keeps.patch
```

- Both apply cleanly on current upstream/main, in order.
- No dependencies on other patches in `core/patches`, and none on each other (0008 touches `startup_resume.py`,
  `web_server.py`, `config_defaults.py`; 0010 touches `session_reaper.py`, `session_auto_continue.py`). Either can
  be applied alone, but see "Why both".
- Then set `desktop.auto_continue.resume_on_startup: true` and restart the backend (quit/relaunch the Desktop app).
  No desktop rebuild is needed: nothing under `apps/` changes.

## Gotchas

- **Off by default.** 0008 does nothing until `resume_on_startup: true` is set. `hermes config set
  desktop.auto_continue.resume_on_startup true` or edit `config.yaml`.
- **Profiles read the launch config.** The sweep reads `load_config()` of the launch `HERMES_HOME` once; that
  config's `enabled`/`freshness_minutes`/`max_attempts` gate the pre-filter for every profile. Profile-level
  config still applies inside `session.resume`'s own guard.
- **Docs-drift CI.** `scripts/ci/check_docs_drift.py` fails on any new `DEFAULT_CONFIG` key that isn't mentioned
  in `website/docs/` or listed in `scripts/ci/docs_drift_baseline.json` (the stock `auto_continue` keys are
  baselined). 0008 adds `desktop.auto_continue.resume_on_startup` without docs, so for an upstream PR document it
  under `website/docs/` (or run `--update`, which is the ratchet's escape hatch, not the intended fix).
  `resume_on_startup_delay_seconds` isn't in `DEFAULT_CONFIG`, so the checker doesn't see it.
- **Private-symbol coupling.** `startup_resume.py` imports `_load` and `_marker_path` from `turn_marker.py` and
  looks up `tui_gateway.server._methods["session.resume"]`. Upstream refactors of either break it at runtime;
  the failure is logged (`session.resume handler not installed` / `startup resume sweep failed`), not raised.
- **The flag is never cleared.** `_exit_keeps_turn_marker` stays on the session dict; that's fine only because
  it's set on the exit path and the process is going away. Don't call `_stop_turns_before_exit` anywhere except
  shutdown, or those sessions will never retire their markers again.
- **Delay vs. readiness.** The 5 s sleep is a heuristic, not a readiness check. On a slow boot (big state.db
  migration) raise `resume_on_startup_delay_seconds`.
- **Sweep also runs under `hermes dashboard`.** It hooks `web_server._lifespan`, which isn't Desktop-only. It
  only acts on Desktop turn markers, and only when the config is on.
- **`hermes update` with local commits.** These are local commits on top of upstream; a plain update that resets
  to upstream drops them. Re-apply with `git am -3 --empty=keep` (or use a fork-merge flow like patch 0007).

## Could this be a plugin instead?

- **0008: mostly yes, as a Python plugin**, not a desktop (`@hermes/plugin-sdk`) plugin. The sweep is
  self-contained: read markers, call the in-process `session.resume` handler. A backend plugin loaded in the
  backend process could start the same thread at load time. The catch: you depend on a plugin hook that runs in
  the web_server process after `tui_gateway.server` installs its methods, and on the private `_methods` /
  `turn_marker._load` symbols. A desktop JS plugin could approximate it by calling `session.resume` over RPC for
  each recent session at app start, but it can't see marker files or writer liveness, so it would resume
  sessions that have nothing to continue.
- **0010: no.** It changes what `_retire_turn_marker` does on the shutdown path inside core; there's no hook
  there. A plugin could monkeypatch `session_reaper._stop_turns_before_exit` and
  `session_auto_continue._retire_turn_marker`, but `method_ctx.bind_module` rebinds those bodies into
  `server.py`'s globals at install time, so patching the module attribute after install may miss the bound copy.
  Fragile; keep it as a core patch (it's 5 lines and a good upstream candidate).
