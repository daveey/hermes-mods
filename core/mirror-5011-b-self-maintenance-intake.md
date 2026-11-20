# Daily self-maintenance intake

Mirror entries: 4474, 4475.

## Behavior

- **4474** — Explicit maintenance declarations route through one configured intake home, which serializes create-or-append by repository, tenant, area and UTC day.
- **4475** — Sender tenant scope is frozen before request hashing and forwarding; explicit tenant selection takes precedence.

## How it works

The public `self_work` contract has exactly `version`, `repository`, `area` and `kind`. Version is integer 1; repositories are `hermes-core`, `hermes-state` and `hermes-mods`; maintenance kinds are `sweep`, `test-gap`, `ci-flake`, `guard` and `post-merge`. Areas are canonical subsystem keys. Ordinary product work omits the declaration; titles are never a classifier.

`kanban.self_work.home` supplies the receiving gateway/profile/board. CLI and tool front doors submit immutable courier envelopes, while worker forwarding validates accepted origin obligations and a fresh authenticated creator lease. Replica discovery is addressing information, not authority. The receiver checks its actual home and database and refuses uncertain or mismatched routes rather than creating locally.

Within one admission transaction an open same-day/area owner receives the new finding as a comment, or a new owner is created. The journal saves the receipt atomically with the write. Immutable request replay returns the prior receipt before changed creation preflight can duplicate work. Sender tenant resolution happens before that identity is frozen, preventing unrelated tenant scopes from merging.

## Code

- `hermes_cli/kanban.py`
- `hermes_cli/kanban_cli_body.py`
- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_db_creation.py`
- `hermes_cli/kanban_origin_creation.py`
- `hermes_cli/kanban_origin_owner.py`
- `hermes_cli/kanban_parser.py`
- `hermes_cli/kanban_placement.py`
- `hermes_cli/kanban_selfwork.py`
- `hermes_cli/kanban_selfwork_batch.py`
- `hermes_cli/kanban_selfwork_discovery.py`
- `hermes_cli/kanban_selfwork_forward.py`
- `hermes_cli/kanban_selfwork_intake.py`
- `hermes_cli/kanban_selfwork_outbound.py`
- `hermes_cli/kanban_selfwork_owner.py`
- `hermes_cli/kanban_selfwork_receiver.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_create_args.py`
- `tools/kanban_tools_schemas.py`
- `website/docs/user-guide/self-maintenance-batches.md`

## Tests

The source suites cover the contract, CLI/tool front doors, daily selection, concurrent admission, transactional rollback, changed-request rejection, tenant precedence, origin adoption, worker authorization, forwarding and immutable replay. They include product controls and missing/malformed home refusals. No live courier or producer rollout was performed for these docs.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_kanban_selfwork.py`
- `tests/hermes_cli/test_kanban_selfwork_admission_boundary.py`
- `tests/hermes_cli/test_kanban_selfwork_batch.py`
- `tests/hermes_cli/test_kanban_selfwork_creator.py`
- `tests/hermes_cli/test_kanban_selfwork_entry.py`
- `tests/hermes_cli/test_kanban_selfwork_forward.py`
- `tests/hermes_cli/test_kanban_selfwork_intake.py`
- `tests/hermes_cli/test_kanban_selfwork_nochat.py`
- `tests/hermes_cli/test_kanban_selfwork_occurrence.py`
- `tests/hermes_cli/test_kanban_selfwork_outbound.py`
- `tests/hermes_cli/test_kanban_selfwork_owner.py`
- `tests/hermes_cli/test_kanban_selfwork_tenant.py`
- `tests/hermes_cli/test_kanban_selfwork_worker_outbound.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

Transport queue acceptance is not an authoritative handoff receipt. Courier route registration and producer enablement are companion deployment prerequisites. Execution placement remains separate from admission authority. Done/archived owners are not reused; batching does not rewrite dependencies, placement or acceptance criteria. For retries across midnight use a stable occurrence-specific idempotency key, never a key reused for the next day's occurrence.
