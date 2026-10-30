# Pull requests run only the Python tests their change can reach

Patches:

- `patches/1019-ci-pull-requests-run-only-the-Python-tests-their-cha.patch`
- `patches/1020-ci-affected_tests.py-reads-BOM-tolerant-and-bounds-i.patch`
- `patches/1021-test-ci-pin-the-planner-s-full-suite-slice-count-to-.patch`

`scripts/ci/affected_tests.py` narrows a pull request's Python test run to the files its change can reach; everything else keeps the full suite.

## Behavior

- `ci.yaml` `detect` runs the planner on the PR's compare-API file list. Selected: changed test files, the transitive import closure of changed modules (ast; function-level imports count), string patch targets, tests that name a changed file by path, plus a smoke set (`tests/ci` and every tree-scanning test, ~132 files).
- The full suite runs for conftest / pyproject / uv.lock / runner / setup-pm changes, a selection over 40% of the suite, a failed or capped compare, the `ci:full-tests` label, and every non-PR event (main push, dispatch, release, release-check).
- The slice count follows `test_durations.json`; each slice recomputes the selection from the same list. Job names say `affected` when the run is narrowed.
- 1020: the planner reads files BOM-tolerant (`utf-8-sig`) and bounds its git calls with a timeout; a git timeout falls back to the full suite like a failed diff.
- 1021: a test pins the planner's full-suite slice count to `tests.yml`'s matrix.

## How it works

`detect-changes` exposes the compare file list; `affected_tests.py` builds the import graph of the repo and emits the selected test files per slice; `tests.yml` consumes the list and falls back to the full suite on any doubt.

## Code

`.github/actions/detect-changes/action.yml`, `.github/workflows/ci.yaml`, `.github/workflows/tests.yml`, `scripts/ci/affected_tests.py`.

## Tests

`tests/ci/test_affected_tests.py`, `tests/ci/test_fast_runner_routing.py` (including the slice-count pin from 1021). Tests were added or changed by these commits; they were not re-run for this mirror.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

The commit message records a replay on 83 red PR slice jobs (2026-10-27..29): 45 would have run the full suite anyway; on the 30 narrowed runs 21 failing files were selected and every skipped failure was not caused by the PR. A change the graph cannot see (data files read by path strings it does not parse) runs only the smoke set; the label `ci:full-tests` forces everything.

## Plugin

None.
