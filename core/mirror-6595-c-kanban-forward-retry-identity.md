# Forward retry identity

**Scope:** Later-run retry identity.

**Source ordinals:** 6519, 6520, 6521, 6522

## Behavior

- After a definitive pre-create binding refusal that was later repaired, a later authenticated run's forward retry derives its own transport request identity, so the immutable earlier request no longer collides. The distinct identity applies only after a definitive no-write binding rejection; exact replay and ordinary metadata conflicts keep the old identity.
- The distinct identity is derived (suffix `:run:<run_uid>` on the idempotency key) only when the create carries an idempotency key, the prior send was `rejected` with `origin_forward_binding_missing`/`handoff_complete` false for the same origin and creator, and the earlier request's run uid differs. Malformed or missing stored result/body shapes (ValueError/TypeError/KeyError/IndexError/AttributeError) make the predicate return False, so the original identity and obligation are retained. That courier refusal stays fatal is pinned only by the test-only commits, not by this production diff.

## How it works

- `kanban_placement.py` changes the key derivation and the predicate.

## Code

Files touched by the series (paths relative to the source tree):

- `hermes_cli/kanban_placement.py`

## Tests

Source test files changed or added by the series:

- `tests/hermes_cli/test_kanban_forward_retry_boundary.py`
- `tests/hermes_cli/test_kanban_placement_identity.py`
- `tests/origin_transport/test_terminal_retry.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6519-6522, 4 commits: 6519, 6520, 6521, 6522) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Two rejected product keys in one authenticated run are both retried.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Transport core.
