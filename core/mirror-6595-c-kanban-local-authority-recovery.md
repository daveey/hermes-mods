# Local answer recovery and authority contract tests

**Scope:** Home-local ActionStore adapter for answer recovery; contract tests.

**Source ordinals:** 6438, 6439, 6440, 6441, 6442

## Behavior

- Desktop local card delivery had no remote exception issuer, and the remote verifier refuses local projections, so replaying the courier would manufacture provenance. A versioned home-local append-only adapter recovers a recorded local answer by exact operation without replaying decisions; the `python -m` entry now calls canonical main; ask source/offer/hold are independently validated; receipts replay immutably; old plugins fail closed on stamped asks.

## How it works

- `kanban_selfwork_local.py`, `_local_source.py`, `kanban_reason_comment.py`, `kanban_terminal_guard.py`.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_db.py`
- `hermes_cli/kanban_reason_comment.py`
- `hermes_cli/kanban_selfwork_exception.py`
- `hermes_cli/kanban_selfwork_local.py`
- `hermes_cli/kanban_selfwork_local_source.py`
- `hermes_cli/kanban_terminal_guard.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_reason_fence.py`
- `tests/hermes_cli/test_kanban_selfwork_callback_contract.py`
- `tests/hermes_cli/test_kanban_selfwork_entry_contract.py`
- `tests/hermes_cli/test_kanban_selfwork_local.py`
- `tests/hermes_cli/test_kanban_selfwork_local_guards.py`
- `tests/hermes_cli/test_kanban_selfwork_local_history.py`
- `tests/hermes_cli/test_kanban_selfwork_module.py`
- `tests/hermes_cli/test_kanban_selfwork_receipt_contract.py`
- `tests/hermes_cli/test_kanban_selfwork_source_contract.py`
- `tests/hermes_cli/test_kanban_selfwork_unblock_wiring.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6438-6442, 5 commits: 6438, 6439, 6440, 6441, 6442) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Local fenced comments remain separate from remote proof. Historical recovery is deliberately restricted to one source-pinned operation, not an arbitrary operation-ID recovery facility; its exact identifier is not reproduced here. Normal versioned delivery is a separate path, and replay never renews expired or spent authority.
- Ordinals 6439-6442 are contract/mutation tests only; no live recovery or admission is claimed.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Authority recovery is in the kernel store.
