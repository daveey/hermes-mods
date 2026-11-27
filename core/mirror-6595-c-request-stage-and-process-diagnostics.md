# Request-stage diagnostics, stream phases and process custody

**Scope:** Read admission stages, result POST stages, Gemini stream clocks, SIGINT, desktop defer.

**Source ordinals:** 6513, 6514, 6515, 6562, 6564, 6577

## Behavior

- Coalesced read admission is traced with fixed-cardinality stages (group/shared admission, runner submit, worker start/end, result boundaries); each shared read has a random read id and every waiter records its join under its own request id while execution keeps the leader id. No arguments, keys, bodies or headers are logged.
- The session-mention result POST path is added to the exact-path stage-observer lists on the Electron and ASGI sides; the Electron transports themselves are not changed in this series. Read-stage events are emitted only when a request-stage trace is active for the request.
- Gemini streaming records bounded per-hop phase clocks (request, headers, decoded text, parsed SSE, translated output) with a random trace and hop index; at most six fixed-schema INFO records per hop plus an end record; raw-text, SSE-event and translated-chunk first-seen phases are kept separate so raw transport activity is not counted as model output; no content is logged.
- Shell-hook child spawn defers callable main-thread SIGINT handlers until the process handle is published, then replays under the tree-cleanup boundary.
- Automatic notices defer behind a verified local Desktop owner instead of falling through to the terminal ownership-card publisher. The proof requires every holder pid to be a birth-matched same-host Desktop registry lease for that session; any registry error fails closed (not deferred). Presentation only: it takes no custody.
- The SIGINT deferral applies only on the main thread with a callable handler; ignored/default handlers and worker threads are untouched.

## How it works

- `web_read_coalescing.py`, `web_read_stages.py`, `web_request_stages.py`, `apps/desktop/electron/request-stages.ts`; `plugins/model-providers/gemini/stream_phases.py`; `agent/shell_hooks.py`; `tui_gateway/gateway_background_holder.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `agent/shell_hooks.py`
- `apps/desktop/electron/request-stages.ts`
- `docs/request-stage-evidence.md`
- `hermes_cli/web_read_coalescing.py`
- `hermes_cli/web_read_stages.py`
- `hermes_cli/web_request_stages.py`
- `plugins/model-providers/gemini/client.py`
- `plugins/model-providers/gemini/stream_phases.py`
- `tui_gateway/gateway_background_holder.py`

## Tests

Source test files changed or added by the series:

- `apps/desktop/electron/request-stages.test.ts`
- `tests/agent/test_shell_hooks_tree_kill.py`
- `tests/hermes_cli/test_web_read_stages.py`
- `tests/hermes_cli/test_web_request_stages.py`
- `tests/plugins/model_providers/test_gemini_stream_phases.py`
- `tests/tui_gateway/test_automatic_desktop_defer.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6513-6577, 6 commits: 6513, 6514, 6515, 6562, 6564, 6577) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Observation only: authentication, deadlines, retries and replay are unchanged.
- The SIGINT deferral does not block or ignore SIGINT in the child or swallow KeyboardInterrupt.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Core server.
