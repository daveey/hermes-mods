# Kanban priority bounds and guarded global normalization

Patches: **2331, 2389–2396**.

## Behavior

Legacy terminal priorities stay historical until a card is reopened; reopening bounds the root and every invalidated descendant in the same transaction and records each original value in a `reprioritized` event. Apply/undo of a priority migration refuse atomically if any recorded card became terminal or disappeared (newly created cards are tolerated on undo). A one-shot, operator-only migration ranks one complete authoritative inventory globally (not per origin), with requested exception cards reserved in top bands (at most 900), and exposes `prepare`, `stage`, `apply`, `restore`, `verify` and `verify-restore` operations on the supervisor CLI.

## How it works

`plan_global` ranks the whole inventory. `prepare` captures raw canonical Board pages at one feed head and `stage` saves an immutable backup plus an attachment on the local owner without changing any priority. `apply_attached` revalidates the manifest from originals (attachment integrity alone is not validation), the writer, ownership and exact open membership/values, then writes only that origin's rows through the normal writer transaction/outbox. A finite feed interval check (`kanban_priority_feed.py`) ignores unrelated traffic (heartbeats, events, runs) but refuses capture/verify if task membership, openness, ownership or priority changed, so global quiescence is not required. Restore needs an explicit `--allow-legacy-rollback` flag plus the same membership/value CAS. The CLI refuses worker, subprocess and delegated-child callers before opening any manifest.

## Code

`hermes_cli/kanban_priority.py` (`bound_reopened`), `kanban_priority_migration.py`, `kanban_priority_executor.py`, `kanban_priority_manifest.py`, `kanban_priority_prepare.py`, `kanban_priority_feed.py`, `kanban_priority_cli.py`, `kanban_parser.py`, `kanban_supervisor_requests.py`; operator runbook `docs/kanban-priority-rollout.md`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_kanban_priority_reopen.py`, `test_kanban_priority_executor.py`, `test_kanban_priority_global.py`, `test_kanban_priority_prepare.py`, `test_kanban_priority_cli.py`, `test_kanban_priority_verify.py`, `test_kanban_priority_manifest.py`, `test_kanban_priority_boundary_cases.py`, `test_kanban_priority_page_limit.py` and `test_kanban_priority_feed.py` (reopen audit, atomic refusals, worker refusal before manifest open, malformed pages/heads, stale inventory, feed interval cases). Live multi-origin rollout and Board sync publication are not covered by these unit tests.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

Each origin applies atomically but origins are **not** atomic together; if one refuses, stop and either restore applied origins or capture a new plan. Capture is an observation, not a lease. A local command receipt is not publication proof: read back through `verify` after normal sync. Restoring above-cap values can be re-clipped by cap-aware imports. Patch 2389 is titled WIP and is completed by 2390–2396; do not stop at it.

## Plugin

Core change; no plugin required.
