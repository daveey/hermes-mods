# Create-path flag validation, creator-source audit and origin index

**Scope:** REST create validation, stored creator-source audit, and a task_id index for origin markers.

**Source ordinals:** 6386, 6492, 6546, 6547, 6548

## Behavior

- REST task creation now rejects nested metadata mandatory flags (pin/require-model spellings) with 400, matching PATCH and bulk, because the create body model now declares metadata so the validator can see it.
- Every Kanban create stores the creator source (author and session) in the audit trail, including interactive and spin-out creates that have no creator task; the source author/session are stamped from the inserted row after diagnostic overrides, and unbound producers store truthful null provenance.
- Owner-local origin marker lookups use a task_id index created at migration and at lazy creation/restoration boundaries, removing a per-projection scan of courier intents.

## How it works

- `plugins/kanban/dashboard/plugin_api.py` declares the metadata field on the create body; `kanban_db_creation.py` stamps the source; `kanban_db_connect.py`/`kanban_origin_creation.py` own the index.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_origin_intent_carry.py`
- `plugins/kanban/dashboard/plugin_api.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_create_entry_parity.py`
- `tests/hermes_cli/test_kanban_origin_intent_index.py`
- `tests/test_kanban_create_flags265.py`
- `tests/test_kanban_hub_admission_audit.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6386-6548, 5 commits: 6386, 6492, 6546, 6547, 6548) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Authority is unchanged: creator-source stamping is audit only.
- The index must be created at every lazy restore path, not just migration.
- The metadata field on the create body is validation-only: it is read by the pre-board flag validator and never reaches the created task; inert values (null/false/0) still create.
- The origin-marker index is a performance change only; no projection or authority policy changes. Measurements in the source commit text are prior-author claims, not re-run.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Create validation and the creation audit sit in the core store and REST layer.
