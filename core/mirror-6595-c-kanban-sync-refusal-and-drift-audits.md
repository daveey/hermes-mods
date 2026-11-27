# Kanban sync refusal audits and diagnostic drift supersession

**Scope:** Receipted policy refusals vs drift, audit composition, fence continuity, partition audits, supersession proofs.

**Source ordinals:** 6395, 6396, 6397, 6398, 6399, 6403, 6404, 6405, 6406, 6461, 6462, 6476, 6563, 6581

## Behavior

- The diagnostic distinguishes receipted policy refusals from real drift; retained refusal audits compose with raw counts without double counting; a busy diff is not drift.
- Historical observation POST status is surfaced without resending immutable intents; diagnostic pulls retry only in a bounded quiet window; HTTP error status is retained.
- A single sync fence is held from diagnostic pull through both audits so a competing sync cannot invalidate a sample.
- Rejected actionability diagnostics are audited as superseded when matching changed portable source facts exist on live and shadow copies (frozen source event required on both; version-only differences do not count) and when terminal done supersession is proven without fabricating sources.
- Partitioned foreign parents are audited only on a fresh cut inside the final reservations, and fresh-audit work is bounded by unresolved candidates, not already verified custody.

## How it works

- Modules: `kanban_sync_refusal_audit.py`, `kanban_sync_diagnostic.py`, `kanban_sync_cli.py`, `kanban_history_residue_*.py`, `kanban_sync_actionability_drift.py`, `_terminal.py`, `kanban_sync_retain_drift.py`, `kanban_rejected_retain_retirement.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_history_residue_board.py`
- `hermes_cli/kanban_history_residue_cut.py`
- `hermes_cli/kanban_history_residue_http.py`
- `hermes_cli/kanban_rejected_retain_retirement.py`
- `hermes_cli/kanban_sync_actionability_drift.py`
- `hermes_cli/kanban_sync_actionability_terminal.py`
- `hermes_cli/kanban_sync_cli.py`
- `hermes_cli/kanban_sync_diagnostic.py`
- `hermes_cli/kanban_sync_refusal_audit.py`
- `hermes_cli/kanban_sync_retain_drift.py`

## Tests

Source test files changed or added by the series:

- `tests/fixtures/kanban_drift_monitor.py.txt`
- `tests/hermes_cli/kanban_sync_fake_board.py`
- `tests/hermes_cli/test_kanban_diagnostic_continuity.py`
- `tests/hermes_cli/test_kanban_diagnostic_window.py`
- `tests/hermes_cli/test_kanban_history_residue_board.py`
- `tests/hermes_cli/test_kanban_history_residue_cli.py`
- `tests/hermes_cli/test_kanban_history_residue_http.py`
- `tests/hermes_cli/test_kanban_history_residue_http_boundaries.py`
- `tests/hermes_cli/test_kanban_refusal_inventory.py`
- `tests/hermes_cli/test_kanban_sync_actionability_drift.py`
- `tests/hermes_cli/test_kanban_sync_actionability_terminal.py`
- `tests/hermes_cli/test_kanban_sync_partition_audit.py`
- `tests/hermes_cli/test_kanban_sync_push_starvation.py`
- `tests/hermes_cli/test_kanban_sync_refusal_audit.py`
- `tests/hermes_cli/test_kanban_sync_rejections.py`
- `tests/hermes_cli/test_kanban_sync_round_followups.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6395-6581, 14 commits: 6395, 6396, 6397, 6398, 6399, 6403, 6404, 6405, 6406, 6461, 6462, 6476, 6563, 6581) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- No transport clearing, replay, receipt fabrication or worker-fence change; the audit is read-only.
- Unknown rows still consume the original fresh-audit cap.
- A matched refusal proves refused transport identity only, never historical payload/enrollment identity, past admission or execution authority. Unknown/unproven refusals and real row differences stay dirty.
- The server-side read endpoint for retained refusal verdicts ships outside this source series; an absent or old serving RPC stays fail-closed.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

The audit reads core sync custody tables and shares the sync fence.
