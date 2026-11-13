# macOS state lane CI routing

Patches: **3253, 3287–3288**.

## Behavior
- The standalone macOS `hermes_state` lane no longer runs on pushes to main; a 4-hourly schedule tests main HEAD instead, a probe-branch push trigger stays unfiltered, and the main workflow keeps masking its own main-push call. Standalone runs collapse without superseding required calls.
- A change to any module that `hermes_state` imports at load time now selects the PR lane. `HERMES_STATE_IMPORTS` lists the 17 first-party modules and joins the lane's path set; a test derives the imports from source with `ast` (module level, try/else/class bodies, `match`/`case` bodies; not function-local imports or `if TYPE_CHECKING:` blocks, and only bare `TYPE_CHECKING` guards count as type-only) and fails until the list matches exactly.
- Transitive imports are deliberately excluded (the closure would start the lane on far more PRs).

## How it works
- 3253 edits the CI workflows and `classify_changes.py` and rewrites the routing tests. 3287 adds `HERMES_STATE_IMPORTS` and the import-surface test. 3288 refines the scanner (bare `TYPE_CHECKING` only; walk `match`-`case`).

## Code
- `.github/workflows/ci.yaml`
- `.github/workflows/hermes-state-macos.yml`
- `scripts/ci/classify_changes.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_macos_state_routing.py`: `test_standalone_runs_collapse_without_superseding_required_calls`, `test_main_push_never_starts_the_standalone_lane`, `test_schedule_backstop_tests_main_and_collapses_with_itself`.
- `tests/ci/test_macos_state_import_surface.py`: `test_scanner_reads_only_load_time_first_party_imports`, `test_surface_lists_exactly_hermes_states_load_time_imports`, `test_each_import_starts_lane_on_pr`; 3288 extends the scanner coverage (no new test names read).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Skew between PRs that land close together is caught only by the 4-hourly schedule, not per main push.
