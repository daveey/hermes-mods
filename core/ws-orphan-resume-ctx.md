# Orphan-race resume tests use a real session.resume ctx

Patch: [`patches/0788-test-tui_gateway-build-orphan-race-resume-ctx-from-a.patch`](patches/0788-test-tui_gateway-build-orphan-race-resume-ctx-from-a.patch)
(fork commit `264ba95bc9`). Test harness only. Product code is unchanged.

## Behavior

The late-RPC reuse cases in `tests/tui_gateway/test_ws_orphan_races.py` no longer build a duck-typed `SimpleNamespace` ctx. They build it from `server._Resume`, the real `session.resume` ctx, and stub only the DB-touching mint and restore steps. Reap assertions are unchanged.

## How it works

`_resume_reuse_live_locked` reads `ctx.lazy`. The old double predates that read, so both the closed-reuse and live-reuse cases raised `AttributeError` on main. A real `_Resume(1, {"omit_messages": True}, "stored")` has every attribute those paths read. `found`, `mint` and `restore` are still stubbed, because these tests drive live-session races, not history reads.

## Code

`tests/tui_gateway/test_ws_orphan_races.py` only. Helper `_resume_ctx()`.

## Tests

The same parametrized cases, now using the helper:

- `test_reconnect_cannot_cross_orphan_interrupt_claim`
- `test_late_rpc_from_closed_socket_keeps_orphan_reap_armed`

Not re-run in this mirror. No product entry path changed, so none is claimed.

## Applying

`git am -3` 0788. Test file only.

## Gotchas

A new attribute read on the resume ctx will not be covered by a duck-typed double. Add the read on `_Resume`, not on the fixture.

Not a plugin.
