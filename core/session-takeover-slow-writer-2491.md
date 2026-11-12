# Take over waits out a live but slow writer; a refused prompt can be resent

Patches: **2476**.

## Behavior

`session.take_over` no longer gives up after the fixed 8 s ACK deadline when the current writer is provably alive on this host. Such a writer is waited out for up to `HERMES_SESSION_TAKEOVER_LIVE_WRITER_WAIT_S` (default 60 s) as long as the handoff stays current. Any writer not provably alive (unstamped, foreign pid namespace, dead, unprobeable) keeps the 8 s `HERMES_SESSION_TAKEOVER_ACK_TIMEOUT_S` deadline. A writer is never forced out. The refusal (4090, `SESSION_NOT_OWNED`) now says the process is alive but did not let go within N s and may be overloaded, mentions an older Hermes CLI only when the holder is a CLI, and is logged.

In Desktop, `takeOverSession` gives `session.take_over` a 75 s request timeout on both the plain and the profile-routed requests, and a legacy identityless prompt send that the server definitely refused (4009 busy; 4090 other than `SESSION_OWNER_UNCONFIRMED`; or a pre-admission capability refusal) clears `legacyAttempted`, so an explicit retry resends it instead of showing "may already have run". Timeouts, disconnects, malformed ACKs and `SESSION_OWNER_UNCONFIRMED` keep the flag.

## How it works

`_live_local_writer_pid(holder)` parses `pid=<n>:pidns=<ns>:...`, requires the pid namespace inode to equal `/proc/self/ns/pid` and `os.kill(pid, 0)` to succeed. `_take_over_durable_writer` polls, re-checking liveness each iteration, with limit `live_s` for a live pid and `ack_s` otherwise (`live_s` is at least `ack_s`). `_unreleased_writer_message` builds the refusal. In `submit.ts`, `isDefiniteSubmitRefusal` decides whether `prepared.legacyAttempted` is reset. Base feature: [session-takeover.md](session-takeover.md).

## Code

`tui_gateway/methods_session_takeover.py`, `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.ts`, `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts`, `apps/desktop/src/app/session/hooks/use-prompt-actions/utils.ts` (`gatewayReason` exported).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tui_gateway/test_session_takeover.py` (live writer released after the ACK deadline, unprovable writer refused at the ACK deadline, live writer refused at the live cap), `tests/tui_gateway/test_cli_durable_takeover.py` (older-CLI case now sets a small live-writer wait), `apps/desktop/src/app/contrib/hooks/use-session-tile-delegate.test.ts` (75 s timeout on both routes), `apps/desktop/src/app/session/hooks/use-prompt-actions/submit-legacy-identity.test.tsx` (busy 4009 refusal retried). Other definite-refusal codes in `isDefiniteSubmitRefusal` have no dedicated test claimed here.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- Liveness is only proven for a writer in the same pid namespace on Linux; `PermissionError` counts as not proven.
- A CLI child alive in the same namespace now also gets the live wait, so tests of the older-CLI refusal must lower it.
- The Desktop request timeout (75 s) is a constant tied to the 60 s default; raising the env var past it would make the client time out first.

## Plugin

Core change; no plugin required.
