# Agent plugin activation from a --profile backend

Patches: **1710** (`7f40a2d49b`, `[daveey] agent-plugins/activate: scope the default profile by name so a --profile backend serves root-home activations`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- `POST /api/agent-plugins/activate` scopes the default profile by name, so a backend started with `--profile` still serves activations meant for the root home.

## How it works

`hermes_cli/web_routers/dashboard_ui.py` resolves the requested profile by name instead of the process's active home.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1710: (subject only)

## Code

`hermes_cli/web_routers/dashboard_ui.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_agent_plugin_activate_home.py`: `test_default_root_home_activates_on_a_named_profile_backend`, `test_launch_profile_home_still_activates_under_itself`, `test_non_profile_home_is_still_refused`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
