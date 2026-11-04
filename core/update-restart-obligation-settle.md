# hermes update: restart obligations settle for unmapped stops and external-checkout runtimes

Patches: **1885** (`dd3dcfe081`, `[daveey] fix(update): settle the restart obligation for an unmapped stop on a live home and for external-checkout runtimes`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- The host restart obligation left by `hermes update` is settled for a stop that maps to no gateway on a live home and for runtimes running an external checkout, so the CLI stops warning that gateways were not restarted when they are current.

## How it works

`hermes_cli/update_cmd_fleet.py`, `update_cmd_fleet_verify.py` and `update_cmd_stale_survivors.py` settle each owed entry against its actual runtime code.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1885: Problem: since 2026-11-03 13:00 UTC every `hermes` CLI call on hermes-hub printed "A previous `hermes update` pulled new code but did not restart running gateways", although the only checkout-backed gateway (default, pid 2542475) was current on ceff8ab7358f. The host obligation (gateway-locks/host-update-restart-*.json, expected_sha ceff8ab7358f) owed: default, askeve-builder, askeve-reviewer (both code 14a1244773e6, askeve-runtime's own pinned core) and an unmapped row {profile: None, pid: 2549548, stopped_unmapped: true}.

## Code

`hermes_cli/update_cmd_fleet.py`, `hermes_cli/update_cmd_fleet_verify.py`, `hermes_cli/update_cmd_stale_survivors.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_update_obligation_row_classes.py`: `test_an_external_checkout_gateway_is_not_owed_a_restart_onto_this_checkout`, `test_a_stale_checkout_gateway_still_owes_the_restart_next_to_an_external_one`, `test_an_unmapped_stop_on_a_home_whose_gateway_runs_the_checkout_is_settled`, `test_an_unmapped_stop_on_a_home_with_no_live_gateway_keeps_warning`, `test_an_unmapped_stop_with_an_unreadable_home_keeps_the_old_rule`, `test_the_manual_sweep_reads_each_unmapped_home_before_the_stop`, `test_the_stale_survivor_sweep_reads_each_manual_home_before_the_signal`, `test_an_unmapped_stop_is_owed_to_its_own_homes_profile_not_to_any_live_gateway`, `test_the_stale_survivor_sweep_records_the_home_of_the_rows_own_profile`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
