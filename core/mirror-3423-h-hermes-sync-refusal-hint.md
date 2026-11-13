# Terminal guard: sanctioned-route hint when refusing hermes-sync

Patches: **3392, 3415**.

## Behavior
- When the terminal lifecycle guard refuses a `hermes-sync` run (lifecycle content or over the scan budget), the refusal explains that hermes-sync runs only from its scheduled pass, names the fast-forward-only mods merge plus the reload log line as the deploy route, and forbids wrapper launchers (`systemd-run`, `nohup`, `setsid`). Other refusals keep the generic text.
- The hint fires only when a command segment actually executes hermes-sync (as its command word after env assignments and wrappers, as the script run by a shell or Python interpreter, or inside an `sh -c` payload, depth-bounded and within the scan budget); a mere mention (`grep x hermes-sync; ./deploy.sh`) gets no hint.
- The hint points at the sync log under the root Hermes home, even when the gateway runs under a profile.

## How it works
- 3392 adds the hint to the refusal in `tools/terminal_tool_guards.py`.
- 3415 ties it to an executed hermes-sync using the guard's own prefix-peeling helpers, and resolves the root home with `get_default_hermes_root(home=get_hermes_home())`.

## Code
- `tools/terminal_tool_guards.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/tools/test_lifecycle_guard_hermes_sync_hint.py`: `test_refused_hermes_sync_names_the_sanctioned_route`, `test_other_refusals_keep_the_generic_text`, `test_hint_follows_hermes_sync_through_wrappers`, `test_a_mere_mention_of_hermes_sync_gets_no_hint`, `test_hint_names_the_root_sync_log_from_a_profile_gateway`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- The hint is advice text only; it does not change which commands are refused.
