# Stable pickup-watcher observation identity

Patches: **4568–4569, 4613–4614** (5011 snapshot).

## Behavior

Host-wide pickup incident bodies no longer change solely because elapsed minutes or sweep time advanced. Registry evidence identity omits observation-time stamps. Dispatcher ticks that change only elapsed minutes, pass counters, or registry read time refresh silently. New targets, reasons, counts, and actual registry fields still append.

## How it works

Host-wide bodies render ready-since timestamps; elapsed minutes remain in the title and target-refresh path. Registry evidence drops `_now_ms()` rendering for readable and unreadable registries. `append_observation` compares the latest delivered observation transactionally after stripping tick-only fields. Rebase reconciliation keeps skip suppression plus a positive changed-target assertion.

## Code

- `hermes_cli/kanban_pickup_observation.py`
- `hermes_cli/kanban_pickup_stall.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_pickup_stall.py`
- `tests/hermes_cli/test_pickup_incident_append.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Task/comment `created_at` still records observation time. Distinct causes remain distinct incidents. Closed-new behavior is preserved. No sleeps or retries were added.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
