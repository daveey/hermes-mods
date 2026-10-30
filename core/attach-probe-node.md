# Attach e2e probe uses installed Node, never PM provisioning

Patches: 1282

- `patches/1282-test-e2e-keep-attach-probe-off-unrelated-PM-provisio.patch` (source `3c2af6ee2e`, fork PR #312, merged as `4f6db7af82`)

Test-only change. No runtime code changes.

## Behavior

The attach-handshake e2e suite (`tests/e2e/core/security/test_attach_handshake.py`) resumes a session with
`hermes --tui --resume <id>` against a probe TUI. Its `_resume` helper used to set `HERMES_SKIP_NODE_BOOTSTRAP=1`.
Under that flag the launcher could still reach the package-manager (PM) runtime acquisition path, so on a runner
without a provisioned PM toolchain the attach test hung or failed on provisioning that has nothing to do with
attachment. The helper now passes the Node binary the suite already requires (`pytestmark` skips without Node) as
`HERMES_NODE`, so the hand-off runs on installed Node.

## How it works

- `_resume` resolves `shutil.which("node")`, asserts it exists, and passes it as `HERMES_NODE` in the child's
  environment in place of `HERMES_SKIP_NODE_BOOTSTRAP`.
- New `tests/e2e/core/security/test_attach_runtime_isolation.py` reruns the real control case
  (`test_genuine_owner_is_attached`: exact socket, resume id, unchanged lease ownership) through a wrapper that
  replaces `pm.ensure` with a function that raises. Any PM provisioning attempt during attach fails the test.

## Code

- `tests/e2e/core/security/test_attach_handshake.py` (`_resume`)
- `tests/e2e/core/security/test_attach_runtime_isolation.py` (new, 45 lines)

## Tests

- `tests/e2e/core/security/test_attach_runtime_isolation.py::test_attach_control_needs_no_pm_provisioning`
- the existing `tests/e2e/core/security/test_attach_handshake.py` cases, which now run on `HERMES_NODE`

This mirror publication did not rerun them. They ran in the fork's CI for PR #312.

## Applying

Apply in series order (`git am -3`). The patch edits `test_attach_handshake.py`, so it needs the attach-handshake
suite from upstream or earlier patches already in place.

## Gotchas

- Mirror history: this commit reached the fork through a merged branch. Its author date sorts it at position 1259
  of `git log --reverse --no-merges`, so the first 1282-1299 pass, which numbered by position, skipped it, and slot
  1282 held a second copy of 1281 (`9117ffc398`). Published ordinals never move, so it was appended as 1282 when the
  duplicate was removed. See [mirror-1299-verification.md](mirror-1299-verification.md).

## Plugin?

No. It is a core test fix.
