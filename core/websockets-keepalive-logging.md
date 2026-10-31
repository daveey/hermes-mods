# websockets keepalive drops Hermes already handles log one INFO line

Patches: 0622 (daveey/hermes `52ea3523cd`), 1415 (`160518392aa`, handshake-timeout race).

## Why

On 2026-10-27 at 20:49:20Z on hermes-hub (and 16:08 PT on the MacBook) errors.log got
`ERROR websockets.client: keepalive ping failed` with a traceback. Self-heal filed it as defect
`agent-ac35b4d534`. Nothing was broken: Kanban sync's feed socket reconnected 1 s later.

```
Problem: errors.log 20:49:20,534 "ERROR websockets.client: keepalive ping failed", traceback through
  sync/connection.py keepalive -> send_context -> TimeoutError -> ConnectionClosedError
  ("sent 1039 (internal error) keepalive ping timeout"). No session; Kanban sync kept working.
Why 1: the Board server (mailbox) stopped answering pings on one board's feed socket for 20 s. It drops every
  board's socket together several times a day (79 drops on the hub that day, 7 boards in the same minute).
Why 2: Hermes already handles this. FeedWatcher logs one INFO line "feed socket closed ... reconnecting in 1s"
  (agent.log 20:49:20.535) and polling covers the gap (hermes_cli/kanban_sync_client.py FeedWatcher.run).
Why 3: the websockets library logs its own ERROR + traceback first, from its keepalive thread
  (websockets 15.0.1 sync/connection.py:784; asyncio/connection.py and legacy/protocol.py do the same),
  1 ms before Hermes' INFO line.
Why 4: Hermes opened the socket without its own logger=, so the record went to the library's default
  `websockets.client` logger and reached errors.log at ERROR. Same for every other call site (event
  publisher, STT/TTS streaming, browser CDP, relay transport, buzz, simplex, google_meet).
Root (happened): no Hermes call site of the library owned its logger, so a handled drop read as a crash.
Root (not caught): no test checked that a handled drop leaves no ERROR, and nothing flagged new call sites.
Recurrence: same class as the Oct 14 fix for Desktop socket keepalive noise (tui_gateway.loop_noise,
  daveey/hermes 687f63c0e9), fixed one site at a time. This fix covers every site and gates new ones.
```

## Why (1415: handshake timeout race)

```
Problem: errors.log on the MacBook 2026-10-30 17:10 PT (load 45-63): "ERROR websockets.client: unexpected internal
  error" with EOFError("stream ended"), twice, while Kanban board feed sockets timed out mid-handshake and reconnected.
Why 1: connect() hit open_timeout under load and closed the stream while the library's reader thread was still
  delivering the handshake response into it; the thread's EOFError is logged by the library as an internal error.
Why 2: the callers already handle it (TimeoutError, reconnect); the ERROR is library noise from a race.
Why 3: ws_logger demoted only the one message "keepalive ping failed" (0622), so this message kept ERROR.
Root (happened): a one-message allow list; the next library message of the same class needed another fix.
Root (not caught): no test drove a handshake timeout against a slow response.
Recurrence: agent-ac35b4d534 (0622); this defect is agent-93fde87ded.
```

## Behavior

- A websockets connection whose peer stops answering keepalive pings logs, instead of
  `ERROR keepalive ping failed` + traceback, one INFO record on the same `websockets.client` logger:
  `keepalive ping failed (ConnectionClosedError: ...); the connection is closed and the caller handles it`.
  `hermes_logging` pins `websockets` at WARNING, so in practice this line is dropped and only the caller's own
  line (e.g. FeedWatcher's INFO "feed socket closed ... reconnecting") records the drop.
- A connect() that gives up on `open_timeout` while the library's reader thread is still feeding the handshake
  response into the already-closed stream makes websockets log `ERROR unexpected internal error` with
  `EOFError("stream ended")` (library race, reproduced on websockets 15.0.1 and 17.4 with no Hermes code). Since
  1415 that record is one INFO line on the same logger (cause included, no traceback); `connect()` still raises
  `TimeoutError` to the caller, which reconnects.
- Every other websockets record (parser failures, server handler errors) keeps its level and traceback. So does
  any other exception under "unexpected internal error": a `ValueError`, or an `EOFError` with a different
  message, stays ERROR with `exc_info`.
- A real socket failure still closes the connection, raises in the caller, and is handled as before.

## How it works

`hermes_cli/ws_logging.py`: `ws_logger(side="client")` returns a `logging.LoggerAdapter` around
`logging.getLogger(f"websockets.{side}")` (so existing level/handler config still applies; websockets accepts
any LoggerLike). Its `log()` rewrites only `level >= ERROR` records whose message is a key of `_HANDLED` and whose active
exception satisfies that key's predicate: drops `exc_info`, logs INFO with the exception's type and text.
`_HANDLED` is a dict message -> exception predicate (1415 changed it from a message set):
`"keepalive ping failed"`: any exception; `"unexpected internal error"`:
`type(exc) is EOFError and exc.args == ("stream ended",)` (exact type and message, so subclasses and other
EOFErrors are not swallowed).

Every production `websockets` `connect()`/`serve()` passes `logger=ws_logger()` (`ws_logger("server")` for
`serve`). `gateway/platforms/yuanbao.py` dials with `ping_interval=None` (no keepalive thread, so no such
record) and is left as is.

Detection: code_health rule **HX015** (`scripts/code_health/py_rules_websockets.py`) flags any
`websockets[.sync|.asyncio|.legacy][.client|.server].connect/serve` call without a `logger=` keyword in
production code (tests, `scripts/`, `evals/` excluded). It matches import aliases via `canonical_tree` and
also names bound by `from websockets... import connect` that the module rebinds (`except ImportError:
connect = None`). The ratchet blocks new findings; existing ones are grandfathered.

## Code

- `hermes_cli/ws_logging.py` (new)
- `scripts/code_health/py_rules_websockets.py` (new), `py_rules.py` (`CHECKERS["HX015"]`), `config.py` (rule)
- call sites: `hermes_cli/kanban_sync_client.py` (FeedWatcher), `tui_gateway/event_publisher.py`,
  `tools/transcription_streaming.py`, `tools/tts_streaming.py`, `tools/browser_cdp_tool.py`,
  `tools/browser_supervisor.py`, `gateway/relay/ws_transport.py`, `plugins/platforms/buzz/adapter.py`,
  `plugins/platforms/simplex/adapter.py`, `plugins/google_meet/{realtime/openai_client,node/client,node/server}.py`

## Tests

- `tests/hermes_cli/test_kanban_feed_keepalive.py`: a real websockets server behind a TCP relay that forwards the
  HTTP upgrade and then swallows every byte; FeedWatcher (library keepalive forced to 0.3 s) must reconnect, log
  its drop at INFO, and leave zero ERROR records; the keepalive record is INFO without a traceback. Red on
  `fd394a4611` with the production traceback, green after.
- `tests/hermes_cli/test_ws_logging.py`: the keepalive message is demoted with its cause; other errors keep ERROR
  and traceback; WARNING/DEBUG pass through; the library logger's level still filters.
- `tests/scripts/test_code_health_ws_rule.py`: HX015 shapes (sync/async/legacy, aliases, splat without logger,
  optional-import fallback, serve) and scope (production yes, tests/evals no).
- `tests/hermes_cli/test_ws_logging.py::test_handshake_timeout_racing_the_response_logs_no_error` (1415): real local
  server, client socket reads stalled 0.5 s, `open_timeout=0.2`; asserts `TimeoutError` and zero ERROR records.
  Red on `3ee37f99fb5` with the production traceback, green after. A sibling test pins `ValueError` and
  `EOFError` with other messages at ERROR with `exc_info`.
- Existing suites of every touched module passed (579 tests across 27 files, incl. buzz, simplex, relay transport,
  google_meet, browser CDP/supervisor, STT/TTS streaming, tui_gateway).
- Untested: the asyncio and legacy keepalive paths are covered only by the shared adapter test, not by a live
  silent-peer test like the sync one.

## Applying

Applies after 0610 (which adds HX014 to the same rule table). New module + one-keyword edits per call site.
1415 edits `hermes_cli/ws_logging.py` and its test; apply after 0622.

## Gotchas

- `hermes_logging` pins `websockets` at WARNING, so the demoted INFO line is normally invisible; the caller's own
  log line is the record of the drop. Tests that need to see it must lower `websockets.client` themselves.
- yuanbao stays a grandfathered HX015 finding (no keepalive); touching its connect call means adding `logger=`.
- 1415 only recognises the exact `EOFError("stream ended")`; a new library wording of the same race would show as ERROR
  again until `_HANDLED` is extended.
- Does not address why the Board server drops all feed sockets together several times a day.

## Plugin?

No: the fix is a keyword at core call sites plus a code_health rule.
