# Desktop kanban model-requirement and recovery transport test harnesses

**Scope:** Test additions.

**Source ordinals:** 6491, 6579, 6580, 6586, 6587, 6588, 6589, 6590

## Behavior

- Test additions only: producer request-body tests for the drawer and New task form model requirement, a core Electron spec driving them in the real app, a manual-dispatch trigger on the Linux core workflow (plus classifier updates), and an offline loopback SSH control-master harness exercising desktop primary-recovery transport (it skips when Linux sshd is missing and, per its source commit, asserts no new production route selector). The only non-test production-tree changes are the `workflow_dispatch` trigger and one classifier path entry.

## How it works

- `model-override-producers.test.tsx`, `kanban-model-requirement.spec.ts` (later revisions reload before opening the drawer and use the shipped task navigation entry), `e2e-desktop-core.yml`, `scripts/ci/classify_changes.py`, `primary-recovery-wire.test.ts`.

## Code

Files touched by the series (paths relative to the source tree):

- `.github/workflows/e2e-desktop-core.yml`
- `scripts/ci/classify_changes.py`

## Tests

Source test files changed or added by the series:

- `apps/desktop/e2e/core/README.md`
- `apps/desktop/e2e/core/kanban-model-requirement.spec.ts`
- `apps/desktop/electron/primary-recovery-wire.test.ts`
- `apps/desktop/src/plugins/kanban/model-override-producers.test.tsx`
- `tests/ci/test_classify_changes.py`
- `tests/ci/test_desktop_core_dispatch.py`

These were read as part of the mirror from the source diffs only. **Source test coverage was NOT executed in this mirror run**; no pass/fail result is claimed.

## Applying

Treat this series (source ordinals 6491-6590, 8 commits: 6491, 6579, 6580, 6586, 6587, 6588, 6589, 6590) as one dependency unit: later commits fix, harden or test earlier ones, so apply them together and in ordinal order rather than cherry-picking. Series-level ordering and the overall mirror procedure are described in [README.md](README.md). Applying means mirroring source into a checkout; it does not install, activate or deploy anything.

## Gotchas

- Acceptance of the Electron spec remained an open gate in the source history; nothing here was run.
- The core Electron spec was revised several times for board-refresh staleness; the last revision opens the card via the shipped navigation hook and explicitly does not claim board-list freshness is fixed.
- Nothing in this document is a deployment or live-behaviour claim.

## Why core, not a plugin?

Test harness.
