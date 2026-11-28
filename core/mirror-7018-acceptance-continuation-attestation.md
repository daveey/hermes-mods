# Attested acceptance continuations for budgeted lane planning

Patch coverage: 6938, 6939, 6940, 6941, 7012, 7013.

## Behavior

A *required acceptance continuation* is a never-run verification child that the completing executor declares necessary to finish its parent's original acceptance (for example a renderer-shaped required check). Before this series such a child stayed lane-held even with admission budget available, because the lane planner only proposed breakage/cross-lane admissions and nothing authenticated required retrospective verification as distinct from new self-work. Now a typed executor attestation recorded at completion lets the existing budgeted lane apply admit it, without widening prose-based admission or renewing authority.

Live entry paths (these are wired into production code, not behind a flag; they only do something when the metadata key or action is used):
- **Producer.** `complete_task` calls `kanban_acceptance_continuations.prepare(...)` inside its transaction, after the receipt recheck and before the run ends, whenever `metadata` carries an `acceptance_continuations` key. The declaration must be `{version: 1, source_body_sha256, items: [{card, criterion}]}` with 1-16 unique items. `prepare` requires a current, owned, genuinely claimed run (a `claimed` event for that run, a run uid; `force` refuses), the parent body digest to equal both the declared digest and the digest stored on the parent's `created` event, each criterion to be a literal substring of the parent body (16-2000 stripped characters), and each child to be open, created by the parent (creator id on its `created` event plus a parent link) and to carry no human ask, containment/refusal protection, scope/safety/spend restriction or execution history. Any failure raises and refuses the whole completion. On success the completed event payload gets `acceptance_continuations` with the run uid and, per child, the child's created-event uid and a scope fingerprint over a fixed field list (title, body, tenant, assignee, workspace, skills, requires, contract, goal and model fields, runtime limit).
- **Creation.** `record_created` now writes `source_body_sha256` (digest of the task body) into every new `created` event; `source_digest` returns `None` when the store has no `body` column and reads the row by column names rather than assuming a row factory (6939/6940). History created before this has no digest and is not backfilled.
- **Edits.** `keep_admitted_receipt` now treats `acceptance_continuations` like `completion_receipt`: metadata edits that add or change it are refused and the admitted value is kept.
- **Projection.** `hermes kanban lanes held --json` adds a `continuation` object per held card from `candidates()`: `eligible`, `reason`, `next_actor` (`lane-planner` or `existing-owner:<card>`) and `source` (parent, completed event uid, run uid). `check()` is read-only and rechecks lineage, parent done with a completed latest run whose event and run carry uids, body digest unchanged, the child's attested item present with matching created uid and scope fingerprint, all prerequisites done, and that the child is held solely at the lane gate. Malformed completion history or creator references defer only that card.
- **Apply.** `kanban_lanes_plan` has a new `admit_continuation` handler. It re-runs `check()` under the write transaction, refuses a paused lane, re-reads the day's admission count (not stale plan state), then delegates to the ordinary locked admit, which still enforces budgets and never-admit policy; the reason is suffixed with the completion uid and one ordinary admission event is appended. Restart retry admits nothing further.
- **Schema.** `KANBAN_COMPLETE_SCHEMA` advertises the optional `acceptance_continuations` metadata property. 6941 then compressed it and the neighbouring `metadata`, `result`, `created_cards` and `artifacts` descriptions to fit the existing prompt byte budget (wording only) and dropped `maxLength: 2000` from the criterion because a local inference backend rejects it; the writer still enforces 2000. `_claim_is_live` moved to `kanban_db_run` and is re-imported in `kanban_db`.
- **Replica-only children (7012, 7013).** A pool worker's board-synced live file is partial: children its run creates are origin-forwarded and exist only in that board's read-only sync-shadow replica. Only `prepare()` falls back to the replica (`open_replica`): when the child is absent from the live file, its lineage, hold check and scope fingerprint are read from the replica, and a missing replica still fails closed. The hold check on a replica child therefore refuses a declaration if the child has run history or a restriction (7013 adds the `ran` case after a mutation that checked the live file instead passed all tests). `check()`/`apply` read only the live connection; a child absent there is deferred as "board evidence unavailable".

The series also adds `docs/kanban-acceptance-continuations.md` (producer metadata format, planner prompt rule, deployment-acceptance note). It states that no existing planner should switch to the action until its installed core exposes the projection, and that deployment acceptance needs live proof beyond source tests.

## How it works

`hermes_cli/kanban_acceptance_continuations.py` (`prepare`, `check`, `candidates`, `admit`, `source_digest`, scope/lineage/hold helpers), called from `kanban_db.complete_task`, `kanban_lanes_plan.py` (`_admit_continuation`) and `kanban_lanes_cli.py` (`held`); `kanban_completion_receipt.py`, `kanban_db_creation.py` and `tools/kanban_tools_schemas.py` carry the metadata guard, creation digest and schema.

## Code

`hermes_cli/kanban_acceptance_continuations.py`, `kanban_lanes_plan.py`, `kanban_lanes_cli.py`, `kanban_completion_receipt.py`, `kanban_db.py`, `kanban_db_run.py`, `kanban_db_creation.py`, `tools/kanban_tools_schemas.py`, `docs/kanban-acceptance-continuations.md`.

## Tests

Read from source; **not executed here**; no fresh run and no native or end-to-end green claim. `tests/hermes_cli/test_kanban_acceptance_continuations.py` (about 21 cases across the three commits): real completion then held projection then plan/CLI apply once; forged/malformed declarations refuse atomically with the run still running; legacy renderer metadata is never authority; changed scope, independent holds (safety, scope stop, budget, containment marker), unlinked child, parent-body edit, unsolicited sibling, paused lane, never-admit, budget reloaded under lock, restart idempotence, metadata edits, malformed history, and synced execution versus creation namespace; replica-only child attestation plus unlinked / no-replica / ran refusals. The lane planner's behavior against a live fleet and the planner prompt rule are **untested** here.

## Applying

Apply in ordinal order; 6939-6941 fix the first commit's schema/digest, and 7012/7013 extend it for replica-only children.

## Gotchas

- Done-parent status and legacy followup/renderer/verified-card metadata do *not* attest necessity; the typed completion payload does.
- A child living only in the replica is attested by `prepare()` only if the replica shows it open and unprotected; apply-time `check()` does not read the replica.
- The attestation attributes the necessity decision to the executor; it is not a semantic classifier.

## Why core, not a plugin?

Planner and completion tooling are core.
