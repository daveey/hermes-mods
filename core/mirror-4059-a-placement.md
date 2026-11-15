# Placement refusal isolation and home-path recognition

Mirror ordinals: 3728, 3735, 3739, 3769.

## Behavior

- A persistent refusal of a pool self-check repin does not suppress the separate origin repin at the same task revision.
- A worker-first ready card with malformed `requires` is not repeatedly reserved by workers that cannot claim it; its origin repin and diagnostic are bounded.
- Home-path placement recognizes lowercase `pushd` with a home operand and commented `cd` commands, without treating prose “CD” or bare `pushd` as home changes.
- The hub-read root-path exemption is conservative around computed shell paths: bare parameters, backticks, traversal and quoted directory changes retain path markers.

## How it works

`kanban_worker_placement._repin_target` returns both its chosen action and the refusal map to update. `_selfcheck_refused` and `_origin_refused` are separate, revision-keyed, bounded maps; persistent 4xx (except 408/425/429) and 501 refusals are remembered, while outage retries retain the existing policy. `not_claimable` keeps its separate backoff.

`kanban_placement_coordination` removes shell quotes/backslashes for directory-change checks, retains paths for `$NAME`/`$*` and any backtick, and therefore avoids granting a read-only exemption based on a partial literal path. `kanban_placement` bounds each home-change scan at the next home change, avoiding repeated suffix scans. `kanban_home_marker_placement` reports the actual relative state path as a home path rather than quoting the intervening transcript.

## Code

- `hermes_cli/kanban_home_marker_placement.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_placement_coordination.py`
- `hermes_cli/kanban_worker_placement.py`

## Tests

Tests were not rerun by this documentation pass. The following describes assertions present in the staged diffs, not fresh execution results.

- `tests/hermes_cli/test_kanban_hub_read_roots.py` — Computed parameters/backticks, split directory-change spellings, traversal, and quoted cd/pushd/popd forms keep state-path evidence; the ordinary literal read remains a control.
- `tests/hermes_cli/test_kanban_pool_deny_runs_on.py` — Fixture setup/teardown now clears both refusal maps so refusal state cannot leak across cases.
- `tests/hermes_cli/test_kanban_state_path_home_forms.py` — Positive home changes, negative prose/bare pushd cases, a large repeated-cd input with a bounded scan time, and the normalized relative-path diagnostic are asserted.
- `tests/hermes_cli/test_kanban_worker_first_pickup_gate.py` — An end-to-end placement-tick case with a JSON string instead of a requires list asserts zero reserves, one origin repin, cleared placement policy, and diagnostics containing the invalid raw requirement.
- `tests/hermes_cli/test_kanban_worker_origin_repin_refusal.py` — Repeated placement ticks ask pool then origin once per revision, reserve nothing, and produce one capability-mismatch event/comment; a new revision permits fresh asks. The later queue-hold fixture adjustment is documented with the queue-reason feature.

## Applying

Use the full ordered mirror series and the **Applying** recipe in [README.md](README.md), including every historical bridge/replay boundary and the mandatory snapshot-bound hostname escape repair plus its repaired-tree check. These ordinals are not independent cherry-picks. This document does not establish replay success, deployment, or live activation; the parent mirror verification supplies replay evidence separately.

## Gotchas

Remembering an origin refusal is not permission to fall back to a reserve. Read-only shell classification is intentionally conservative, not a shell execution sandbox. The timing assertion is a test contract, not a benchmark result from this documentation pass.

## Plugin?

No. These changes belong to core lifecycle, persistence, or admission boundaries; a plugin cannot reliably replace those internal invariants.
