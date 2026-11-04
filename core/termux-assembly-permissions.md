# Termux assembly: tools dir mode and test mount dir

Patches: **1784** (`442362e61a`, `fix(tests): unique, self-cleaning docker mount dir for termux assembly test [daveey]`); **1785** (`d0b54a133d`, `fix(termux): create assembly tools dir 0755 regardless of host umask [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- The assembly tools dir is created 0755 regardless of host umask (1785); the test uses a unique, self-cleaning docker mount dir (1784).

## How it works

`scripts/termux/assembly_permissions.sh`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1784: The payload dir under HERMES_TEST_DOCKER_MOUNT_ROOT was named after tmp_path.name, which repeats across runs; the runner-temp mount root on the long-lived docker-ci runner outlives a run, so any leftover made every later run fail with FileExistsError. Use mkdtemp under the mount root and remove it in a finalizer (chowning container-owned leftovers back via the pinned image).
- 1785: On umask-0002 hosts (Ubuntu user-private groups; the ephemeral CI runners) prepare_assembly's mkdir left .environments-*/tools group-writable, breaking the no-group/world-writable output contract. The test now runs the harness under umask 0002 so it pins this (red on the old script).

## Code

`scripts/termux/assembly_permissions.sh`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/scripts/test_termux_assembly_permissions.py`: `test_assembly_publishes_facts_as_termux_uid_and_returns_host_owned_output`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
