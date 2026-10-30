# Restart-notice opt-out written after gateway boot is honoured

Patches: 1132.

## Behavior

- A `gateway_restart_notification: false` written to a served profile's `config.yaml` after the multiplexing gateway booted suppresses the next shutdown home-channel notice (and the online notice).
- An unreadable, malformed or absent value falls back to the boot-time cached value.

## How it works

`gateway/config.py` gains `restart_notification_enabled_on_disk(home, platform, cached)`, reading config layers via `config_loader`; a top-level `<platform>:` block wins over `platforms.<platform>`, and `extra` is also consulted. The gate moved into a new mixin `gateway/run_shutdown_notice_gate.py` (`GatewayShutdownNoticeGateMixin`), and `_notice_allowed(..., profile_home=...)` calls it for the home-channel shutdown pass.

## Code

- `gateway/config.py`, `gateway/run_shutdown.py`, `gateway/run_shutdown_notice_gate.py`

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. `tests/gateway/test_planned_restart_notice_multiplex.py`: `test_served_profile_opt_out_gets_neither_shutdown_nor_online_notice`, `test_opt_out_written_after_boot_silences_the_next_shutdown`, and parametrized `test_restart_notification_enabled_on_disk`.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

Only the shutdown home-channel pass re-reads the file; other notice paths still use boot-time config unless the tests above say otherwise. Malformed config is swallowed at DEBUG level by design.

## Could this be a plugin instead?

No: gateway shutdown code is core.
