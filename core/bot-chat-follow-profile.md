# Bot Chat sessions follow their profile's selection

Patches: **1762** (`f87e1d7dc9`, `wip: checkpoint managed Bot Chat profile selection regression [daveey]`); **1764** (`2f3477913c`, `[daveey] fix(gateway): managed Bot Chat follows profile; held pick ends durably`); **1767** (`e1d37fab6c`, `[daveey] fix(gateway): in-process Bot Chat follows profile; intent on physical target`); **1768** (`3dccfc97ac`, `[daveey] fix(gateway): unchanged follow selection never rerolls a class allocation`); **1769** (`5d91524c4b`, `[daveey] fix(gateway): a held pick ending onto a class default never rerolls the next turn`); **1770** (`cebd8c5b27`, `test: share the composed home fixture without a shadowed import (health F811) [daveey]`); **1815** (`fdc396c03a`, `test(gateway): settle Bot Chat follow turns before the next mutation`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Managed Bot Chat follows its profile and a held pick ends durably (1762/1764); in-process Bot Chat does the same and the switch intent names the physical target (1767).
- An unchanged follow selection never rerolls a class allocation, including after compaction (1768) and when a held pick ends onto a class default (1769).
- Test fixtures share the composed home without a shadowed import (1770) and settle each turn before the next mutation (1815).

## How it works

`gateway/session_profile_selection.commit_selection` remembers `{selection, physical}` and the pick the receipt now holds; `session_policy.effective_selection` reports an ended pick.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1762: (subject only)
- 1764: - session_policy: effective_selection() reports a recorded pick that ended on the same profile read. - session_profile_selection: the owner's fenced commit (epoch + generation + receipt policy) drops model.selection_profile from the receipt when the pick ended, then republishes the live policy, so a composer hold never resurrects when the profile returns to its original target. - session_managed_worker: one profile read feeds route, selection commit and hold end. - test: align the owner's state.db with its profile home (root conftest re-points hermes_state.DEFAULT_DB_PATH after first import...
- 1767: - session_ingress/session_local_profile: an unmanaged (desktop, in-process) following session commits its turn's effective selection before _handle_message and holds that SAME profile read for the turn's policy_scope (one read for commit + build); a held pick's end republishes the released receipt for the turn. - session_profile_selection: the switch intent names local_physical_target (what the in-process build and reserve_admission_worker use after compression), not the logical id. The remembered selection includes the physical target and the receipt's frozen pick, so compaction or an expl...
- 1768: commit_selection minted a fresh switch intent whenever [model, provider, physical, frozen pick] changed or no memo existed, so a compaction (new physical target) or a first commit with no memo replaced an unchanged class:deep allocation and moved the session to another provider with no profile edit.
- 1769: commit_selection remembered the selection with the frozen pick of the policy BEFORE the release, while the receipt then held the released policy. The next turn's expected policy is the released one, so the unchanged selection looked new, minted another intent and replaced the class allocation (provider move with no profile edit). Remember the pick the receipt now holds.
- 1770: (subject only)
- 1815: _turn returned as soon as the handler recorded its agent build, before the drain committed the turn's settlement. On a throttled runner a session.mutate issued right after raced that commit: a pick read a stale runtime revision (error reply, KeyError 'result') or arrived while the turn lease was still live (deferred pick, cold build on the old exact pin). Wait for the admission to reach 'terminal'.

## Code

`agent/model_routing_switch_intent.py`, `gateway/session_ingress.py`, `gateway/session_local_profile.py`, `gateway/session_managed_worker.py`, `gateway/session_policy.py`, `gateway/session_profile_selection.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_bot_chat_follow_entry.py`: `test_class_profile_default_follows_off_exact_pin`, `test_commit_refuses_stale_owner_generation_or_policy`, `test_follow_after_explicit_pick_consumed_by_another_build`, `test_hold_ending_onto_a_class_default_never_rerolls_after`, `test_in_process_bot_chat_follows_profile_across_exact_pin`, `test_in_process_composer_hold_until_next_profile_edit_then_follows`, `test_in_process_turn_reads_the_profile_once`, `test_managed_follow_after_compression_uses_the_reserved_physical_target`, `test_managed_hold_ending_onto_a_class_default_never_rerolls_after`, `test_managed_unchanged_class_selection_never_rerolls`, `test_missing_memo_class_selection_still_corrects_an_exact_pin`, `test_pending_follow_intent_moves_with_compaction_unconsumed` (+2 more)
- `tests/gateway/test_bot_chat_managed_follow.py`: `test_managed_bot_follows_profile_across_exact_pins`, `test_new_pick_after_ended_hold_holds_until_next_profile_edit`, `test_ordinary_exact_pin_does_not_follow_profile`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
