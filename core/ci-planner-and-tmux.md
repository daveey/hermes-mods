# CI source graph and tmux prerequisites

Patches: 1139 1156.

## Behavior

- The test planner receives the source graph checkout needed for change detection.
- The Python test job verifies tmux availability and installs it when absent before running tests that drive the real CLI through tmux.

## How it works

Workflow checkout configuration supplies the planner graph. The test-job prerequisite step mirrors the end-to-end job’s tmux preparation instead of relying on a particular runner image.

## Code

- `.github/workflows/ci.yaml`
- `.github/workflows/tests.yml`

## Tests

Source regression coverage:

- `tests/ci/test_detect_planner_checkout.py`
- `tests/ci/test_e2e_tmux_required.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

Workflow-structure tests are not a live hosted CI execution or an actual package-manager installation. No hosted CI success is claimed.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
