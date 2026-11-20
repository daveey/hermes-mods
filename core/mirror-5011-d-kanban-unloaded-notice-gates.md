# Kanban unloaded gates: skip remote notices, notices and guarantee passes when inputs did not change

Patches: **4679, 4680, 4711**

- 4679 (`00f807d83e`): perf(kanban): gate remote_notices, notices and guarantee on the inputs the hub really changes
- 4680 (`ebbcfbc18d`): test(gates): drop wall-clock sleep from guarantee last_active test
- 4711 (`70607520f6`): perf(kanban-unloaded): gate remote_notices on terminal-row prints, reuse swept cards, ignore settled open-chat links

Mirrored in the 4653–4752 pass (batch d). Source tests were not freshly executed by this mirror pass; the notes below come from the normalized patches.

## Behavior

- The notice sweep treats an in-flight claim held by a live, young process (with nothing new past its cursor) as held, not pending; recovery is re-evaluated each probe so dead holders, aged claims and new events still run the pass.
- The remote-notices sweep skips a ledger claim held by another live process; settled links in chats held open by the gateway and unsettled links to archived/moved chats no longer keep the pass hot.
- Replica token movement no longer re-arms the gate on every terminal-row rewrite: it is keyed on a per-row notice print; ledger commits are not a key component; the cold snapshot is cached per replica token and swept cards are reused.
- The guarantee gate uses `last_active` only as a lookback verdict, and an unclaimed notice inside its grace window is treated as a clock (due time), not work.
- No notice, wake or unlink outcome changes.

## How it works

`tui_gateway/kanban_unloaded_gates.py` computes `Inputs()` keys for the three passes; the plugin hook cache file is regenerated for the changed gate identity. Regression tests reproduce the always-hot shapes. 4680 removes a wall-clock sleep from the guarantee `last_active` test.

## Code

- `owned/cache/plugin_hooks.json`
- `tui_gateway/kanban_unloaded_gates.py`

## Tests

Landed with the source commits; not rerun here. Names are tests the patches added (a file with no names was modified only).

- `tests/tui_gateway/test_h2_gate_mutations.py`: `test_guarantee_key_ignores_a_talking_chats_last_active`, `test_guarantee_key_moves_when_last_active_leaves_the_lookback_window`, `test_unclaimed_notice_inside_grace_is_a_clock_not_work`
- `tests/tui_gateway/test_kanban_unloaded_gates.py`: `test_live_foreign_inflight_claim_does_not_hold_the_notice_sweep_hot`, `test_live_inflight_claim_runs_when_its_holder_dies`, `test_live_inflight_claim_runs_when_it_ages_out_or_an_event_arrives`, `test_remote_probe_skips_while_another_live_process_holds_the_claim`, `test_remote_probe_live_holder_on_an_owed_card_is_settled_for_the_pass`, `test_remote_replica_commit_during_the_cold_read_still_publishes_the_proof`, `test_remote_probe_ignores_a_rewrite_of_a_terminal_row_whose_notice_inputs_did_not_change`, `test_remote_probe_reuses_the_swept_cards_while_the_replica_token_stands`, `test_remote_probe_open_chat_with_every_notice_settled_is_quiet`, `test_remote_probe_open_chat_with_an_unsettled_notice_stays_hot`

## Applying

Do not apply these patches independently. Apply them as part of the full segmented recipe in the mods/core README **Applying** section, in series order; they depend on earlier core files and are not certified standalone. To re-implement on a newer upstream, port the Code files above and keep the listed tests green.

## Gotchas

- Every probe must still return pending for own claims, dead holders, aged claims, undelivered events and unarchived chats.
- The owned plugin-hook cache JSON changes with gate edits; keep it in sync.
