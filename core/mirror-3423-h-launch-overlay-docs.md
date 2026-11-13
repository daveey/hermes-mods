# Launch overlay documentation: live env until multiplexing

Patches: **3391**.

## Behavior
- Docstring and comment correction only: the launch environment / `TERMINAL_*` overlay is described as the live process environment until multiplexing is active, and frozen only once it is. No runtime behavior changes.

## How it works
- 3391 rewords the `bind_state` `owner_secret_scope` docstring, `launch_profile_runtime_scope` docstring/comment, and a scope comment in `web_host_terminal`, matching `_launch_env()` returning the live env before multiplexing.

## Code
- `agent/runtime_scope.py`
- `hermes_cli/web_host_terminal.py`
- `tui_gateway/launch_profile_policy.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

No test files in these patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
None recorded.
