# CI: state-store mini lane follow-up nits

Patches: **3669**.

## Behavior
- The gate-lanes comment in `ci.yaml` now states the right direction: required PR jobs never queue behind a main-push CI run on the single mini runner.
- The routing test asserts an invariant (any push trigger on the standalone state workflow has a branches filter and no pattern matching main or master, via fnmatch) rather than pinning the full trigger set.
- The workflow file documents that scheduled-run failures are picked up by the hub's existing main-CI watcher, and to update its SCHEDULED entry if the file moves. No new watcher was added.

## How it works
3669 edits two workflow files (comments/docs only) and tightens one test.

## Code
- `.github/workflows/ci.yaml`
- `.github/workflows/hermes-state-macos.yml`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/ci/test_macos_state_routing.py`: `test_main_push_never_starts_the_standalone_lane`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Scheduled-red alerting depends on an external watcher outside these patches; untested here.
