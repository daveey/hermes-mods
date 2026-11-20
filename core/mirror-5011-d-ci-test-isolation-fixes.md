# Test isolation: watchdog clocks, serve-proof home, git maintenance, flaky-CI script path

Patches: **4661, 4667, 4671, 4694**

- 4661 (`bf6447c04e`): test(agent): isolate resumed-warning watchdog observation [daveey]
- 4667 (`23606a5ed1`): [daveey] test(serve-proof-home): isolate parser test from the detached default-plugin installer
- 4671 (`c012b9ee1c`): [daveey] stop detached maintenance in copied attestation fixtures
- 4694 (`4077e682db`): [daveey] fix(ci-flaky): pin the checkout when flaky_kanban.py runs as a script

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- The turn-liveness watchdog test drives real ticks with consumer-local wall/monotonic clocks, so the "resumed during warning" case no longer races real sleeps. Production watchdog policy is unchanged.
- The serve-proof-home parser test disables lazy plugin installs so a detached default-plugin installer cannot write into the sentinel home during its snapshot.
- Copied code-attestation fixture repos persist `maintenance.auto=false` before the first commit so detached git maintenance cannot delete loose objects mid-copy.
- `scripts/ci/flaky_kanban.py`, when run as a script, puts its own checkout on `sys.path` ahead of PYTHONPATH and site-packages, so a stale installed `hermes_cli` snapshot cannot hide newer modules.

## How it works

All four are test/CI-side changes except the script import-root branch in `flaky_kanban.py`. Each pairs a regression that reproduces the original failure (decoy package, real child-launch trace, real clocks) with the fix.

## Code

- `scripts/ci/flaky_kanban.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/agent/test_turn_liveness_watchdog.py`: modified only
- `tests/ci/test_flaky_kanban_import_root.py`: `test_script_run_prefers_its_checkout_over_a_stale_hermes_cli`, `test_script_run_resolves_the_selfwork_producer_from_its_checkout`
- `tests/hermes_cli/test_code_attestation_fixture.py`: `test_committed_core_copy_has_no_automatic_housekeeping`
- `tests/hermes_cli/test_code_attestation_route.py`: modified only
- `tests/hermes_cli/test_serve_proof_home.py`: modified only

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- No production behavior changes, except the flaky script's sys.path ordering.
- The git-maintenance fixture fix depends on git versions that can detach auto-maintenance; older git is unaffected.
