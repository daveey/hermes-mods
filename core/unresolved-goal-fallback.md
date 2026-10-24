# Unresolved goal fallback (draft, not deployed)

## Behavior

An opt-in pre-final plugin can attach `unresolved_fallback: {goal, evidence_refs}` to a hold. Core retains that structured rejection instead of the candidate diagnosis. Budget exhaustion, hold-cap exhaustion, and unavailable rechecks render an explicit runtime boundary. Stop/cancel retains precedence. Legacy plugins are unchanged.

## How it works

`pre_final_gate` structurally validates the fallback; the plugin is responsible for evidence and authorization validation. Stop-gate state carries the typed fallback to the finalizer. Held text is private scaffolding and removed from returned history. Hook worker threads copy ContextVars so profile-local settings and auxiliary credentials resolve in the caller's scope.

## Code

Draft core PR: https://github.com/daveey/hermes/pull/48

- `agent/pre_final_gate.py`
- `agent/turn_stop_gates.py`
- `agent/turn_final_response.py`
- `agent/turn_finalizer.py`

Commits: `26ab40ebed4f3c8031f15f302663033cd6c5bb0a`, `88e38164b66c1da05894c58146df5b41ca968ce6`.

## Tests

78 targeted core tests passed, including full shared-agent text-stop tests for streaming/nonstreaming budget/cap failure, Stop, history cleanup, successful recheck and profile-context propagation. Four output-transform/lifecycle tests also passed. All 12 core lint checks passed.

Combined coach discovery and scripted agent-loop tests cover CLI/Desktop platform settings, cap and unavailable/uncertain rechecks. These are not transport-level CLI executable or Electron UI acceptance tests. Production activation is unverified and enforcement remains off.

## Applying

Do not enable enforcement yet. The changes are on draft branches, not the released numbered patch series. After review and merge, regenerate the public fork patch series and verify application before deployment. Source checkout changes are not evidence that an already-running backend reloaded.

## Gotchas

This is not a remediation guarantee. The companion coach guard still lacks authoritative owner readbacks and complete ancestry recovery. Uncertain first checks fail open with telemetry; confirmed rejected candidates must not silently become accepted merely because a recheck failed. No task is created, no permission broadened, and no self-heal or Inbox policy changed.

## Plugin?

Detection belongs in the coach plugin. Core is needed only because a plugin cannot otherwise control the budget/error fallback that reuses its rejected candidate.
