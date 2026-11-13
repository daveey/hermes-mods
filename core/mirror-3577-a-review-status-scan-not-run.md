# CI review_status: a scan with no verdict is "did not run"

Patches: **3563**.

## Behavior
- When a supply-chain scan or dependency-bounds check writes no verdict (flag unset, empty, or anything other than `true`/`false`), `review_status.py emit` publishes one `action_required` result titled "The <check> did not run", saying the PR was not checked and pointing at the failing job step. Previously it published `[]`, which looked like a clean scan.
- `true` still reports findings and `false` still publishes `[]`.

## How it works
- 3563 makes `emit` treat only `true` and `false` as verdicts and gives each `Finding` a `check` label for the title. Prompt-surface and lockfile emits run only after a producer step that always writes `changed=`, so they are unaffected; the workflow file is unchanged.

## Code
- `scripts/ci/review_status.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_repo_guard_checks.py`: `test_emit_reports_a_check_that_wrote_no_verdict_as_not_run`, `test_a_scan_with_no_verdict_survives_the_supply_chain_merge`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Depends on the scan step failing closed (writing no `found=`) while the emit step still runs after it.
