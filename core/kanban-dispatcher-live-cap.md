# Embedded Kanban dispatcher re-reads max_in_progress every tick

Patches: **1829** (`8f3ca18b32`, `fix(kanban): embedded dispatcher re-reads kanban.max_in_progress every tick [daveey]`), **1833** (`632e807b48`, `fix(kanban): a bad cap read keeps the cap in force, not the boot cap [daveey]`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- The gateway-embedded dispatcher re-reads `kanban.max_in_progress` on every tick, so lowering it in `config.yaml` takes effect without a gateway restart (1829).
- An unset, invalid or unreadable cap read keeps the last good cap in force rather than reverting to the boot value, so a transient read error never raises the cap (1833).

## How it works

`gateway/kanban_watchers_dispatcher.py` reads the cap per tick and remembers the last valid value.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1829: hermes-hub booted its gateway at 04:18Z with max_in_progress=16; config.yaml was lowered to 7 at 05:15Z, yet the gateway kept claiming (dashboard 12 -> 15/7) until an unrelated restart at 05:59Z logged max_in_progress=7.
- 1833: 8f3ca18b32e (PR763) made the embedded dispatcher re-read kanban.max_in_progress every tick, falling back to the BOOT value when the read was unset/invalid/ unreadable. Hub sequence boot 16 -> config 7 -> one bad read reopened 16, the opposite of that commit's "a transient read error never raises the cap".

## Code

`gateway/kanban_watchers_dispatcher.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_kanban_dispatcher_live_max_in_progress.py`: `test_lowered_cap_applies_on_next_tick_without_restart`, `test_unset_cap_keeps_boot_value`, `test_lowered_cap_refuses_claims_through_real_budget_gate`
- `tests/gateway/test_kanban_dispatcher_live_max_in_progress_fail_closed.py`: `test_bad_or_missing_value_after_lowering_keeps_lowered_cap`, `test_config_file_deleted_after_lowering_keeps_lowered_cap`, `test_config_read_raising_after_lowering_keeps_lowered_cap`, `test_explicit_value_still_raises_and_lowers_after_a_bad_read`, `test_bad_read_after_lowering_refuses_claims_through_real_budget_gate`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
