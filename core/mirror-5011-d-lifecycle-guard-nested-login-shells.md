# Lifecycle guard: nested login shells, process-substitution feeds, wrapper peeling

Patches: **4676, 4677, 4697, 4698, 4732**

- 4676 (`1e608c843f`): [daveey] lifecycle guard: walk printed text a `< <(…)` feeds into nested login shells
- 4677 (`29b5f9d052`): [daveey] recognize continued stdin process-substitution openers
- 4697 (`d5e7156bca`): [daveey] lifecycle guard: peel command wrappers before classifying a nested login shell
- 4698 (`ff9060bba0`): fix(lifecycle): walk nested wrappers through the landing command [daveey]
- 4732 (`38b0017c43`): [daveey] Consume exec -a argv0 before walking nested login shells

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- Printed text fed to a login shell by `< <(...)` (including compound commands, redirection-first form and backslash-continued openers) is scanned in nested scope like its pipe twin.
- Wrappers (`env`, `nice`, `timeout`, `exec`, `command`, `sudo`) are peeled one at a time before classifying `su`/`runuser`/`doas`/`sudo` login launchers, and nested wrappers are followed through the landing command; `exec -a argv0` consumes its operand.

## How it works

`cron/lifecycle_guard.py` and `cron/lifecycle_guard_nested_shells.py`, tested via the session-alias suite (real Bash markers and clean twins).

## Code

- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/cron/test_lifecycle_guard_session_aliases.py`: `test_continued_process_opener_real_bash_and_clean_twin`, `test_continued_process_opener_inert_text`, `test_nested_shell_wrapper_boundary`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- The guard is lexical; refusals fail closed, and clean twins must stay allowed.
