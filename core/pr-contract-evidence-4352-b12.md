# PR completion contracts and history reduction evidence

Patches: **4280, 4282–4283** (4352 snapshot).

## Behavior

Missing PR completion metadata and metadata contradicting the persisted contract receive distinct actionable diagnostics. A mismatch is not presented as a CI failure or silently rebound to a different PR.

Test-only follow-ups cover suite-app activity and merge-cutoff siblings, plus numeric type-key equivalence in ready-floor reduction.

## How it works

Acceptance reports the expected contract and supplied published_pr, classifying missing versus contract_mismatch before running acceptance checks. Exact matches continue into those checks.

The ready-floor analysis compares warm int-to-float-to-int snapshots with cold and uncached reductions and visible diagnostics. Its counter-analysis identifies an equivalent type-key mutation; it is explicitly not a production optimization or a killed mutant.

## Code

- `docs/ready-floor-type-key-counter-analysis.md`
- `hermes_cli/kanban_pr_acceptance.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_pr_contract_diagnostics.py`
- `tests/hermes_cli/test_kanban_pr_fallback.py`
- `tests/plugins/test_kanban_history_ready_reductions.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Repository-only contracts still need an exact PR URL at completion. Equal numeric cache behavior is limited to the current admitted scalar snapshots and reduction, not arbitrary future reducers or mutable objects. Source analysis test counts are historical, not mirror reruns.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
