# Worker policy root and comment-writer guard nits

Patches: **3717, 3718**.

## Behavior

- 3717: an unusual `TMPDIR` can no longer make a durable path acceptable as `HERMES_TEST_POLICY_ROOT`. `TMPDIR` is honoured alongside `/tmp`, `/var/tmp` and `/dev/shm` only when ephemeral: not an ancestor of the account home or `HOME`, and inside the account only under `.hermes/cache/scratch`. Previously, with `TMPDIR` set to the home directory, a durable `.hermes` path under it was honoured, an honoured empty root turned `worker_enabled()` off, and `refusal()` failed open.
- 3717: a non-unknown resource answer clears the unknown-streak record, and an unknown answer without an exception records the streak (test-pinned).
- 3718: the comment writer's `agent_ancestor` now recognizes an interactive `ssm-session-worker` process by its 15-character comm prefix, so a shell started via an SSM session stays attributed as human even when the process name is truncated (for example after `exec -a`). Before, it walked on to the agent process and recorded `agent`. The old behavior only lowered authority.

## How it works

- 3717 adds a `SYSTEM_TEMP_ROOTS` constant and an ephemeral-`TMPDIR` check in `worker_compute_capacity._test_policy_override`, and documents the override in the `worker_enabled()` docstring.
- 3718 compares `name[:15] == INTERACTIVE_SSM[:15]` in `kanban_comment_writer`.

## Code

- `hermes_cli/worker_compute_capacity.py`
- `hermes_cli/kanban_comment_writer.py`
- `tests/hermes_cli/test_worker_compute_capacity.py`
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py`
- `tests/hermes_cli/test_kanban_cli_comment_writer.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_worker_compute_capacity.py`: `test_an_odd_tmpdir_never_admits_a_durable_policy_root`.
- `tests/hermes_cli/test_worker_resources_unknown_alarm.py`: `test_refusal_answers_feed_the_streak_without_an_exception`.
- `tests/hermes_cli/test_kanban_cli_comment_writer.py`: `test_interactive_ssm_session_stays_human` (full and comm-truncated names).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas

- The 3717 override is a test/policy hook; only ephemeral locations are honoured.
