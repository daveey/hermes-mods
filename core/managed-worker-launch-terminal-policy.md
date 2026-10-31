# Managed-worker launch terminal policy scrub

Patches: 1330, 1331, 1332.

## Behavior

A secondary profile's managed worker must get the environment a standalone `hermes -p X` would build, without the launch profile's terminal policy. `strip_launch_profile_env` removes launch residue (launch `.env` keys, config-mapped `TERMINAL_*`) when the target home differs from the launch home. Two derived settings were not in the config map, so they leaked: `TERMINAL_DOCKER_IMAGE_PINNED` and `TERMINAL_HOME_MODE`.

- 1331 added both names to the scrub set. Values in the launch home are kept, and values the target's managed dotenv keys declare are preserved.
- 1332 then made `home_mode` a member of `TERMINAL_CONFIG_ENV_MAP`, so the shared config bridge rebuilds it from the target profile's `config.yaml`; only `TERMINAL_DOCKER_IMAGE_PINNED` stays in the explicit extra set.
- 1330 is test isolation: the worker-env test deletes any inherited `TERMINAL_*` first, since worker shells carry derived flags.

## Code

`tools/environments/local.py` (`strip_launch_profile_env`), `hermes_cli/config.py` (`TERMINAL_CONFIG_ENV_MAP`).

## Tests

- `tests/gateway/test_managed_worker_profile_env.py::test_secondary_worker_env_drops_launch_terminal_policy_and_env_settings` (now sets pinned and home-mode vars and clears inherited `TERMINAL_*`)
- `::test_derived_terminal_policy_preserves_launch_and_managed_values` (kept for the launch home, dropped for a profile, case-insensitive, kept when managed). It was written for 1331; after 1332 its home-mode expectations rely on the config-map path. This mirror did not run it.
- `::test_target_home_mode_is_rebuilt_by_both_terminal_bridges` (`build_profile_terminal_scope` and `apply_terminal_config_to_env` both yield the target's `home_mode`)
- `tests/hermes_cli/test_web_host_terminal.py::test_host_terminal_resolver_applies_selected_profile_home_and_config` now starts with `TERMINAL_HOME_MODE=real` so the target's `profile` must come from config.

This mirror task did not execute any of these.

## Applying

Apply 1330, 1331, 1332 in order. No config.

## Gotchas

Any future derived `TERMINAL_*` variable outside the config map needs adding to the scrub set or to the map. No plugin involved.
