# Test runs share a host-wide pytest cap

Patch **0542** (source `ffd03f1dec`).

## Behavior

`scripts/run_tests_parallel.py` runs each test file in its own pytest subprocess and sizes its pool to
`os.cpu_count()`. Each run did that on its own. With several agents testing at once (one run per
worktree), four runs started 64 pytest processes on a 16-core Mac. The load average passed 250, and the
gateway's event loop kept missing its Discord heartbeat.

Now every per-file attempt runs only while it holds one of `HERMES_TEST_HOST_SLOTS` host slots, shared
by every run on the machine. Concurrent runs split the CPUs instead of multiplying them.

| Setting | Default | Meaning |
|---|---|---|
| `HERMES_TEST_HOST_SLOTS` | `os.cpu_count()` | Pytest file processes allowed at once on the host; `0` turns the cap off |
| `HERMES_TEST_SLOT_DIR` | `/tmp/hermes-test-slots-<uid>` | Directory holding the slot lock files |
| `HERMES_TEST_SLOT_HELD` | set to `1` in each file subprocess | A run started from inside a test skips the cap, so it never waits on the slot its own parent holds |

A run that has to wait says so once: `(waiting for host test slots: other runs hold all N in <dir>)`.
`run_tests.sh` forwards the three variables through its hermetic `env -i`. Non-POSIX hosts are uncapped.

## How it works

- `scripts/run_tests_host_slots.py`: `host_test_slot(on_wait=)` is a context manager. It tries
  `flock(LOCK_EX | LOCK_NB)` on `<dir>/<n>.lock` in random order and polls every 0.1 s when every slot
  is taken. A slot is a held file descriptor, so a run that crashes or is killed frees its slots.
- `scripts/run_tests_parallel.py`: `_run_one_file_once` wraps the attempt (now `_run_one_file_attempt`) in
  `host_test_slot`. The attempt sets `HERMES_TEST_SLOT_HELD=1` in the child's environment.
- `scripts/ci/classify_changes.py` lists the new module with the test harness, so editing it keeps the
  full CI lane set.

## Tests

- `tests/scripts/test_run_tests_host_slots.py`:
  - a third holder waits until one of two slots frees, and `on_wait` fires once;
  - a SIGKILLed holder process frees its slot;
  - a nested run (`HERMES_TEST_SLOT_HELD=1`) skips the cap;
  - `0` slots disables it;
  - the runner's attempt waits for the only slot, and its child sees `HERMES_TEST_SLOT_HELD=1`.
- `tests/scripts/test_run_tests_shell.py::test_shell_runner_forwards_host_slot_settings`:
  `run_tests.sh` keeps `HERMES_TEST_HOST_SLOTS` and `HERMES_TEST_SLOT_DIR`.
- The runner fixtures in `tests/scripts/test_run_tests_parallel.py` and `tests/ci/test_flaky_kanban.py`
  copy the new module next to the runner.

## Applying

`git am -3`. It is independent of other patches.

## Gotchas

- Each worktree runs its own copy of the runner. Worktrees cut before this patch keep the uncapped
  runner until they rebase.
- The cap counts pytest file processes, not their threads or grandchildren. A file that starts its own
  pool still adds to the load.
- `uv sync` bytecode compilation for a fresh test environment (`pip_compileall`, one process per core) is
  outside the cap.

## Follow-up patches (0544, 0569, 0572)

Patch **0572** (source `5b8e544396`) makes the cap per host:

- `host_slot_count()` reads `HERMES_TEST_HOST_SLOTS`, then `tests.host_slots` in the Hermes `config.yaml` under the home directory
  (`HERMES_TEST_SLOT_CONFIG` overrides the path), then `os.cpu_count()`. A laptop the user works on sets a few slots in its own
  config, so agents' tests never take every core and nothing has to inherit an env var. Without PyYAML the one key is read by regex. `0` disables the cap.
- `acquire_host_slot()` / `release_host_slot()` are split out of `host_test_slot()`.
- `tests/conftest.py` makes a direct `pytest` run of the tree (not via `run_tests.sh`) hold one slot for the whole session and
  set `HERMES_TEST_SLOT_HELD=1` for what it spawns. xdist workers skip it (the controller holds the slot). A failure to take a slot is advisory and never fails the run.
- Tests (the last four in `tests/scripts/test_run_tests_host_slots.py`): config sets the cap when the env does not (env still wins);
  config parses without PyYAML; a missing or unset config keeps `cpu_count`; a direct pytest session waits for and holds the only slot (two probe processes).

Patches **0544** and **0569** are lint fixes in `scripts/run_tests_host_slots.py` (Windows footguns; the `/tmp` slot-dir default marked deliberate for the `no-tmp` rule). **0544** also touches `hermes_cli/always_on_dashboard.py`. No behavior change.
