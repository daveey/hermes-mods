# Test and CI flake hardening

Patches: 2795, 2801, 2811, 2813, 2814.

- **2795** (`410640ba96`, test(state-repair): snapshot deadline test pins the _monotonic seam)
- **2801** (`546f0e0a0a`, test(owned-subprocs): cleanup tolerates a tree process that exits before its kill)
- **2811** (`19e8f15882`, test(browser): the SIGKILL owner re-checks until it owns its daemon, as browser_exec does)
- **2813** (`ca144ace46`, test(admission): hold the lock on an event in the reserve/extend timeout test)
- **2814** (`09ee730d46`, fix(kanban): keep deflake guidance independent of home runners)

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2795: the state-DB interrupted-snapshot test started its fake clock at 0.0 so a mutant reading the shared clock survived; the fake now starts 10**6 s ahead and counts seam reads.
- 2801: owned-subproc cleanup kills through one Process object, checks create time, treats a vanished process as cleaned up, and `_alive` counts psutil `STATUS_DEAD` as gone.
- 2811: the SIGKILL-owner test re-checks until it owns its harness daemon, like `browser_exec`, and gives the death wait 60 s.
- 2813: the admission lock test holds the lock on an Event, widens the short-try bound to 5 s and releases via a timer.
- 2814: `flaky_kanban.py` card body guidance no longer references home-runner specifics.

## How it works

- Only 2814 touches non-test code: `scripts/ci/flaky_kanban.py`.

## Code

- `scripts/ci/flaky_kanban.py`

## Tests

- `tests/hermes_state/test_state_db_repair_non_destructive.py`, `tests/hermes_cli/test_owned_agent_subprocs.py`, `tests/tools/test_browser_use_cli_owned_daemon.py` (`test_a_sigkilled_owner_takes_its_harness_daemon_with_it`), `tests/hermes_cli/test_admission_fence.py`, `tests/ci/test_flaky_placement.py` (`test_deflake_template_is_portable`).

Test files touched by these patches:

- `tests/hermes_state/test_state_db_repair_non_destructive.py`
- `tests/hermes_cli/test_owned_agent_subprocs.py`
- `tests/tools/test_browser_use_cli_owned_daemon.py`
- `tests/hermes_cli/test_admission_fence.py`
- `tests/ci/test_flaky_placement.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Timing-sensitive tests; the source notes used SIGSTOP/SIGCONT duty-cycle repros that the mirror did not repeat.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: test and CI-script changes.
