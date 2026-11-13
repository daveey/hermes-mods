# Kanban remote stand-aside lease matching and held-card wake recovery test

Patches: **3554, 3555**.

## Behavior
- The Desktop remote sweep stands aside when the unexpired turn-lease holder is either verified gateway (the profile's own gateway or the multiplexed default gateway), avoiding repeated "deferred: a turn is running" claim/release loops.
- A lease on a compression lineage root stands aside for a link on the tip; after release exactly one lineage row is delivered.
- With no verified gateway, there is no stand-aside.
- A probe dropped for a held card comes back once the hold lifts (re-registered, or the card is woken through `_lost_wake`); this was a test-only addition and no bug was found.

## How it works
3554 replaces the single `canonical_gateway_pid` candidate with `verified_gateway_pids(profile)`, listing the profile home's live gateway pid then the live default gateway pid, deduplicated; a failed lookup drops only its own candidate. `gateway_lease_pid` matches the unexpired lease holder against each through `gateway_owner`; it stays lease-only and pid-verified. 3555 only changes a comment in `_wake` in `kanban_wake_check.py` (the probe-row delete and episode reset apply to every `_wake` producer that reaches a held card) and adds a recovery test.

## Code
- `tui_gateway/kanban_remote_fifo.py`
- `tests/gateway/test_kanban_remote_lease_stand_aside.py`
- `hermes_cli/kanban_wake_check.py`
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_kanban_remote_lease_stand_aside.py`: `test_a_lease_held_by_either_verified_gateway_stands_the_sweep_aside`, `test_a_lease_on_the_compression_root_stands_aside_for_a_link_on_the_tip_then_delivers_once`, `test_verified_gateway_pids_lists_the_profile_and_the_multiplexed_default_gateway`, `test_no_verified_gateway_means_no_stand_aside`.
- `tests/hermes_cli/test_kanban_stale_wake_terminal.py`: `test_a_probe_dropped_for_a_held_card_comes_back_when_the_hold_lifts`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Reading the lease once per lineage id is kept deliberately, since lineage ids may in principle map to distinct roots.
