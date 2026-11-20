# Watch-gated idle annotation refresh

Mirror entries: 4763.

## Behavior

Idle annotation holders avoid repeated database rebuilds when their watched inputs have not changed. Display-only demand uses a 60-second renewal fallback; recent strict demand retains the 20-second renewal.

## How it works

Per-scope signatures combine database and sidecar file identity/stat information, database change counters, WAL-index headers and mutation cues. Missing inputs participate in the signature; unreadable inputs are treated as changed. A verified scope arms a signature/due-time gate. Changes, explicit kicks, foreign cues and expiry wake real verification. Watch-verified display copies can remain displayable for up to 75 seconds while the signature is unchanged; moved inputs and ordinary poll-published copies retain the shorter display bound. Strict callers still require their existing exact-copy freshness checks.

## Code

- `hermes_cli/kanban_annotation_disk.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_watch.py`
- `hermes_cli/kanban_session_activity.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Holder idle/floor/narrow/host suites cover unchanged ticks, changed and unreadable inputs, immediate kicks, strict-demand expiry and display aging. Watch tests exercise same-stat rollback-journal and WAL commits so file metadata alone cannot falsely certify an unchanged database.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/hermes_cli/test_kanban_annotation_holder_floor.py`
- `tests/hermes_cli/test_kanban_annotation_holder_idle.py`
- `tests/hermes_cli/test_kanban_annotation_holder_narrow.py`
- `tests/hermes_cli/test_kanban_annotation_host.py`
- `tests/hermes_cli/test_kanban_annotation_host_regressions.py`
- `tests/hermes_cli/test_kanban_annotation_watch.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

Time-derived state and inputs not reflected by watched files can wait until the fallback. Display trust is not strict read-your-writes authority, and this change does not certify a whole-loop CPU target.
