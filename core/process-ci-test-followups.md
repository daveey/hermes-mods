# Process cancellation and mirror test follow-ups

Patches: **0257 0262 0263 0345 0347 0348 0357**.

## Behavior

Process-tree cleanup re-reads the live descendant tree if its stop budget expires, rather than signalling only a stale first snapshot and leaving a late-forked child alive (0257). Stream cancellation distinguishes an interrupted/cancelled request's transport outcome from an ordinary uncancelled transport failure (0357). Test synchronization changes avoid treating scheduler delay as a product failure.

## How it works

| Patch | Change and boundary |
|---|---|
| 0257 | `agent/deadline.py` increases the stop budget to five seconds and merges a fresh live-tree rescan on expiry. The fork-race probe adds deterministic budget-exhaustion coverage. |
| 0262 | Subagent lifecycle event waits use a bounded 30-second load-tolerant ceiling; test-only, not a runtime latency promise. |
| 0263 | Fork-race probe waits boundedly for SIGKILL to land before judging survivors. |
| 0345 | Maps the fork noreply contributor identity in `contributors/emails/daveey@users.noreply.github.com`; attribution only. |
| 0347 | Nix guard assertion drains command output under `pipefail`, avoiding an early-reader exit causing a spurious pipeline failure. This repeats the change represented by 0353; both source identities are retained. |
| 0348 | Replaces fixed short wall-clock assumptions in fork-race tests with load-tolerant bounds and event waits. |
| 0357 | Synchronizes stream cancellation plus websocket/update-lock test handoffs. |

## Code

`agent/deadline.py`, `evals/cron_timeout_fork_race.py`, `agent/chat_completion_helpers.py`, `agent/chat_completion_stream_monitor.py`, `nix/checks.nix`, and the contributor mapping above. Test-only edits are in the suites listed below.

## Tests

Source coverage, **not executed by this documentation pass**:

- `tests/agent/test_deadline_fork_race.py` and `evals/cron_timeout_fork_race.py`: live process/fork cleanup probe, exhausted stop budget and eventual signal observation.
- `tests/agent/test_subagent_lifecycle.py`: event-driven lifecycle synchronization.
- `tests/agent/test_cancelled_stream_transport_error.py`: `test_cancelled_stream_transport_outcome`, parametrized by ending/cancelled/interrupted state.
- `tests/gateway/test_buzz_websocket.py`, `tests/hermes_cli/test_update_lock_protocol.py`: handoff synchronization.
- `nix/checks.nix` is the changed Nix check definition, not evidence a Nix build ran. Contributor attribution has no runtime regression test in 0345.

Run Python suites with `bash scripts/run_tests.sh <files>` in the isolated applied checkout. Full-series apply/tests and any Nix evaluation/build are pending the mirror owner. No production process cleanup was performed here.

## Applying

Use the complete ordered series. Retain provenance for both 0347 and 0353; the mirror owner decides duplicate apply treatment rather than forcing the repeated hunk. See [mirror-ci-source-fixes.md](mirror-ci-source-fixes.md) for the existing Nix/CI changes.

## Gotchas

Longer test ceilings are not sleeps or performance guarantees. Event-driven waits remain bounded. Source regression coverage must not be reported as a fresh passing run; old mirror totals do not verify this expanded series.

## Plugin relationship

Deadline cleanup, stream transport handling, CI checks and contributor mappings are core/build infrastructure, not disk-plugin behavior.


## Additional merged CI followups

Patches **0327, 0332, 0333, 0337, 0354, 0374–0375** retain the eager-reopen readiness wait, duplicate process-tree deadline fixes, classifier curl fallback, bounded fast-check runner lane and retirement fallback documentation. 0332/0333/0354 are explicit empty duplicates of0257/0263/0348, verified by stable patch IDs. 0338 preserves the final five-minute detect timeout while retaining the curl fallback. Source tests cover deadline process cleanup and eager session readiness; no new hosted runner is provisioned by applying these patches. CI workflow declarations alone do not prove deployed runner capacity.

Code: `agent/deadline.py`, `evals/cron_timeout_fork_race.py`, `tests/agent/test_deadline_fork_race.py`, session reopen tests, `.github/actions/detect-changes`, `.github/workflows`, and CI runner documentation. Apply the entire ordered series using `git am -3 --empty=keep`; this is core/CI work, not a plugin.

## Routing CI fixtures and cron tick ordering (0440, 0442)

Patch **0440** (`a3c3c9e928`) makes the routing CI fixtures and approval batching deterministic
(`tests/tui_gateway/test_approval_unadvertised.py`, `test_protocol.py`, gateway child-notice admission). Patch
**0442** (`bb5e855908`) asserts async cron tick ordering instead of elapsed time (`tests/cron/test_parallel_pool.py`).
Test-only changes.
