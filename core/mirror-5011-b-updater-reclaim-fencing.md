# Exact-attempt updater reclaim fencing

Mirror entries: 4497, 4542, 4543, 4544, 4545.

## Behavior

- **4497** — Reclaim optionally records validated explicit-operator attribution and an exact minimum core target in the durable event.
- **4542** — Updater pause intent is committed before worker termination so concurrent crash observers cannot charge that authorized stop as a failure.
- **4543** — The involuntary source-receipt inventory follows the extracted reclaim implementation rather than its facade.
- **4544** — Persisted intents carry operator attribution through observer recovery.
- **4545** — Recovery atomically adopts only the exact abandoned intent and run identity, never a replacement attempt.

## How it works

CLI `--urgency-source explicit_operator` and `--interrupt-target` are both-or-neither, accepted only with the exact core-update requeue reason and a lowercase 40-hex target. DB validation precedes signalling. Absent fields preserve legacy unknown attribution.

The updater's local intent records current run, claim lock, worker PID/start fingerprint, executor identity and token before termination. Crash/expiry/runtime observers respect this durable pause decision. Process polling is outside the write transaction; closing uses compare-and-swap on the selected attempt. Updater pauses preserve prior unrelated failures, while ordinary operator reclaim retains its separate reset semantics.

An unwound or dead executor leaves a recoverable command rather than an expiring failure exemption. Recovery first atomically checks the same token, abandoned owner and running attempt, adopts ownership, then terminates the intent's PID/fingerprint and closes that exact identity. Stale selection is a no-op. Shared settlement drops finished/stale commands and relinquishes execution of unfinished ones for another observer. Stored attribution is revalidated and preserved in the resumed event.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_core_update_requeue.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_manual_reclaim.py`
- `hermes_cli/kanban_owner_recovery.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_update_reclaim_intent.py`

## Tests

Source tests cover parser/help/CLI-to-event attribution, invalid input before signalling, legacy payload shape, local and primary-board termination races, modern/legacy custody, prior strikes, concurrent reclaimers, unreadable identity, exceptions and dead executor recovery. Recovery cases explicitly replace or finish an attempt between selection and action and assert the replacement is untouched; another test checks attribution survives resumption. Tests were not freshly run here.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_reclaim_attribution.py`
- `tests/hermes_cli/test_kanban_source_receipt_sites.py`
- `tests/hermes_cli/test_kanban_update_reclaim_primary_race.py`
- `tests/hermes_cli/test_kanban_update_reclaim_race.py`
- `tests/hermes_cli/test_kanban_update_reclaim_recovery.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

EOF or SIGTERM alone is not evidence of a neutral updater pause. Intent authority is local and deliberately not replicated as Board authority. There is no automatic expiry into a charged crash. Explicit attribution is audit data, not authorization to interrupt arbitrary work or bypass the exact-reason validation.
