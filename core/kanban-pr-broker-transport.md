# Profile-scoped PR acceptance transport

Patch: **0278**, source commit `ca145ec36e04a7db3271e2df91622dc7adbd91a2`.

## Behavior

Kanban PR acceptance resolves GitHub CLI through the platform resolver, including the user bin fallback. A profile can explicitly configure `kanban.pr_acceptance.gh_command_prefix` as an argv list for an installed absolute executable. This permits an external credential broker to execute the resolved GitHub command without returning credentials to the parent process. Empty configuration preserves direct execution.

Missing executables and malformed wrapper configuration produce sanitized `setup` receipts. Authentication refusals remain `auth`; captured stderr is not persisted in those receipts. This transport does not waive required checks or make an unverified PR acceptable.

## How it works

`_api` builds the profile-isolated environment, resolves `gh`, prepends the configured argv and executes without a shell. `_gh_command_prefix` binds the target home and secret scope while loading effective configuration and resets both afterward. Configuration interpolation therefore uses the assignee's secrets, not the launch profile's. Wrapper validation rejects non-list values, invalid arguments, relative executables and missing executables.

## Code

- `hermes_cli/kanban_pr_acceptance.py`: transport, prefix validation, setup receipts.
- `hermes_platform/resolver/github.py`: executable fallback.
- `hermes_cli/config_defaults.py`: default empty prefix.
- `website/docs/user-guide/features/kanban.md`: configuration documentation.

## Tests

On Linux, the applied public series passed all **11 tests** in `tests/hermes_cli/test_kanban_pr_transport.py`, using `bash scripts/run_tests.sh tests/hermes_cli/test_kanban_pr_transport.py -j1` in an isolated standalone clone. Cases cover missing CLI, actual executable lookup in user bin, synthetic child-only credential vending, cross-profile isolation, auth receipt redaction, invalid wrappers, malformed configuration and assignee-scoped interpolation.

Full ordered **276-patch** `git am -3` succeeded at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, with upstream `0c771d7a5ba7742bd336adb98d30058ba545e327` and fork `ca145ec36e04a7db3271e2df91622dc7adbd91a2`. Existing privacy scrubs and patch 0242 adaptation were retained. This mirror run did not test live credentials, Desktop rendering, Windows, or the full suite.

## Applying

Apply the complete ordered series from the merge-base; this patch is not independent of earlier PR acceptance changes.

## Gotchas

The broker is an explicit per-profile opt-in, not a shared supervisor credential fallback. Install and authorize it separately; do not put secret values in the argv configuration or public documentation. Setup and authentication receipts are distinct from CI failures.

## Plugin?

The broker itself can be external. The acceptance gate and platform executable resolver require the core patch; no plugin activation is necessary for the default direct mode.
