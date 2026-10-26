# Unresolved goal fallback (draft, not deployed)

## Behavior

An opt-in pre-final plugin can attach `unresolved_fallback: {goal, evidence_refs}` to a hold. Core retains that structured rejection instead of the candidate diagnosis. Budget exhaustion, hold-cap exhaustion, and unavailable rechecks render an explicit runtime boundary. Stop/cancel retains precedence. Legacy plugins are unchanged.

## How it works

`pre_final_gate` structurally validates the fallback; the plugin is responsible for evidence and authorization validation. Stop-gate state carries the typed fallback to the finalizer. Held text is private scaffolding and removed from returned history. Hook worker threads copy ContextVars so profile-local settings and auxiliary credentials resolve in the caller's scope. The exact holding callback owns its recheck; removal or replacement cannot be accepted by an unrelated callback. Buffered gates run before verification interim emission. Successful owner acceptance clears the old rejection even when another pre-final gate subsequently holds.

## Code

Draft core PR: https://github.com/daveey/hermes/pull/48

- `agent/pre_final_gate.py`
- `agent/turn_stop_gates.py`
- `agent/turn_final_response.py`
- `agent/turn_finalizer.py`

Commits: `26ab40ebed4f3c8031f15f302663033cd6c5bb0a`, `88e38164b66c1da05894c58146df5b41ca968ce6`,
`dfd0c50d1f3977f7275dbd2baa4a98ab558e4485`,
`626102e336f83a826a80f6dba8c8a70ff7ba9e24`.

## Tests

78 targeted core tests passed, including full shared-agent text-stop tests for streaming/nonstreaming budget/cap failure, Stop, history cleanup, successful recheck and profile-context propagation. Four output-transform/lifecycle tests also passed. All 12 core lint checks passed.

The ownership/verification follow-up ran 124 tests across 13 files. A parent adversarial regression
then reproduced stale rejection after an accepted owning recheck plus another gate hold; after the
fix, 64 focused tests across five files passed, including 20 full-loop ownership/order/interim tests.
The combined plugin/repository suite passed 501 tests (one platform-dependent SQLite skip).
These are overlapping suites, not an additive unique test total.

Combined coach discovery and scripted agent-loop tests cover CLI/Desktop platform settings, cap and unavailable/uncertain rechecks. These are not transport-level CLI executable or Electron UI acceptance tests. Production activation is unverified and enforcement remains off.

## Applying

Do not enable enforcement yet. The changes are on draft branches, not the released numbered patch series. After review and merge, regenerate the public fork patch series and verify application before deployment. Source checkout changes are not evidence that an already-running backend reloaded.

## Gotchas

This is not a remediation guarantee. The companion coach guard has a conservative local running-task readback only; remote ownership and complete ancestry recovery remain incomplete. Uncertain first checks fail open with telemetry; confirmed rejected candidates must not silently become accepted merely because a recheck failed. No task is created, no permission broadened, and no self-heal or Inbox policy changed.

## Plugin?

Detection belongs in the coach plugin. Core is needed only because a plugin cannot otherwise control the budget/error fallback that reuses its rejected candidate.

## Pending initial-assessment correction

Independent review reproduced an earlier ordinary gate starving the coach before its
first assessment, so a one-iteration turn returned the rejected diagnosis. The draft
core now supports opt-in `assess_before_holds`; coach enables it only with enforce-mode
buffering. Pending callback identity still runs first. Legacy callback order is unchanged
without an opt-in. Complete isolated agent turns with real discovery of ask-gate and coach
reproduced two failures before the fix and pass for CLI/Desktop platform values afterward.
These are backend-loop tests, not actual client transport acceptance.

The ancestry increment recovers user rows only through verified compression parents,
bounded at child creation, and uses a unique exact current-request anchor for restart
recovery. Fork/reset/delegate children do not inherit parent permission. Ambiguity,
overflow and unsupported multimodal durable data remain UNKNOWN. Mid-turn session-ID
rotation is still unverified. The combined suite passed 515 tests, with one SQLite
host-dependent skip. Independent review still requests changes: exact-action/approval
proof, remote/scheduled ownership and return liveness, holdout/private replay, transport
acceptance, CI, mirror/apply and activation remain required. Do not activate this draft.


## Native local result-consumer evidence (draft)

The companion guard now consumes `tui_gateway/kanban_consumer_health.py` receipts. Native
`session_notifications.py` registers its actual consumer thread/session and records a receipt
only after a successful board poll. Receipts bind profile home, origin key and board path;
they expire after 15 seconds and fail on stopped/dead/finalized/refused consumers. A receipt
is not a future delivery guarantee. Other processes, unloaded consumers, remote gateways
and scheduled dispatcher liveness are not certified by this API.

Tests: `tests/tui_gateway/test_kanban_consumer_health.py` verifies registration, successful
poll requirement, scope/rotation, wrong-thread rejection, refusal, expiration and shutdown.
The coach `test_goal_owner_gate_liveness.py` exercises native board polling with an unreadable
board negative; `test_goal_ownership_liveness.py` covers isolated completion collection to an
origin test sink. These do not claim real client transport acceptance.


## Private-candidate transport delivery (draft)

Buffered assistant candidates no longer bypass the gate as `reasoning.available`
progress previews. Native provider reasoning and unbuffered legacy previews are
unchanged. An unresolved gate outcome now sets a failed/error result with a
non-retryable `pre_final_gate_unresolved` runtime descriptor, rather than presenting
an incomplete goal as a successful gateway `message.complete`.

Tests: `tests/agent/test_pre_final_delivery_channels.py` covers held progress text,
accepted delivery, error outcome, runtime error classification and legacy previews.
The companion `plugins/coach/tests/test_goal_disposition_transport.py` executes
isolated Python CLI stream-json dispatch, stdio JSON-RPC and loopback WebSocket
using the production Desktop gateway handler, a local SSE model and real SessionDB.
All nine cases pass after reproducing progress leaks and incorrect terminal status
on both gateway routes. Analysis-only and explicit action-denial controls pass.
This does not claim Electron UI, authenticated serve startup, interactive PTY,
interrupt-RPC, or production activation coverage. Enforcement remains disabled.


## Native scheduled-dispatch evidence (draft)

`gateway/kanban_dispatcher_health.py` records successful embedded-loop board passes with
exact board/profile/lane, per-board monotonic observation time, bounded expiry, boot and
process-start identity and kernel flock ownership. Pause, failed ticks, cancellation and
stop invalidate evidence. One-shot/dry-run execution does not publish. The native dispatch
result now identifies admission past global budget/memory gates; a disabled or suppressed
tick cannot certify scheduling. `kanban_db_dispatch_queries.py` owns the unchanged lane query.

Tests: `tests/gateway/test_kanban_dispatcher_health.py` exercises actual isolated watcher
loops, cross-process reads, scope/expiry/identity, zero-budget and critical-memory suppression,
and delayed publication. Seven focused gateway/consumer files passed 42 tests; five adjacent
native-dispatch files passed 18 tests. These are not production activation evidence.

Coach scheduled ownership composes this with a future native wake and the native origin
result-consumer receipt. It does not certify unfinished dependency parents, remote tasks,
standalone daemons, or process-notification routes. Linux only; unsupported systems fail
UNKNOWN. The native runtime directory is a trusted same-UID state boundary; readable process
identity plus flock does not authenticate evidence against arbitrary malicious same-UID writers.
No new enforcement setting, service restart or live activation is included.
