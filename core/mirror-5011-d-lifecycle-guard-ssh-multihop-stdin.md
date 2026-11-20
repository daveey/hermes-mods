# Lifecycle guard: follow multi-hop ssh to the stdin consumer

Patches: **4723, 4724, 4725, 4726, 4727, 4728**

- 4723 (`55d8ccf83e`): [daveey] lifecycle guard: follow multi-hop ssh to the stdin consumer before walking a file as a remote shell
- 4724 (`03d3bb7bda`): [daveey] lifecycle guard: parse ssh options after the destination before reading the remote command
- 4725 (`8c002b43f3`): [daveey] lifecycle guard: follow a remote shell's -c payload to its stdin reader
- 4726 (`d8db17842b`): [daveey] lifecycle guard: past the first ssh hop, read stdin as data only for allowlisted consumers
- 4727 (`429e1de15b`): [daveey] lifecycle guard: past the first hop, python is a data consumer only when stdin is its program
- 4728 (`a13b9b4c91`): [daveey] lifecycle guard: thread the ssh hop depth through _shell_input after the wrapper-peel split

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Each ssh hop is followed (capped at four, fail closed beyond) to the command that reads stdin: data consumers keep the file as data; shells, hopless logins and remote user switches walk it as a nested script.
- Options after the destination and shell `-c` payloads are parsed; past the first hop only allowlisted consumers count as data, and python counts only as `python [flags] [-]`.

## How it works

`cron/lifecycle_guard_nested_shells.py` (4728 threads the hop depth after a refactor split) with session-alias tests.

## Code

- `cron/lifecycle_guard_nested_shells.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/cron/test_lifecycle_guard_session_aliases.py`: `test_multi_hop_ssh_data_consumer_reads_stdin_as_data`, `test_multi_hop_ssh_shell_still_walks_stdin_as_nested_script`, `test_multi_hop_python_launcher_executes_stdin_as_shell`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Allowlist, not denylist, beyond the first hop.
