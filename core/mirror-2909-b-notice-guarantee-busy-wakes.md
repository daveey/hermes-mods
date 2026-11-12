# Notice guarantee: busy close-out wakes are deferred, not final

Patches: 2802, 2803, 2842, 2843.

- **2802** (`8569969db9`, fix(notices): a busy close-out wake is deferred and offered again, not stamped final)
- **2803** (`b333168ef8`, fix(notices): a row stamped final busy by the older core is offered again; comment states the real 4.25 h busy span)
- **2842** (`ae91b28ccc`, test(notice guarantee): two backends' passes over one legacy final-busy row wake it once)
- **2843** (`7f99b4f794`, test(notice guarantee): fix two-backends test comment wording (review t_01a1d08cc2a8c713 nits))

Mirrored in the 2788-2848 pass (part b); see [mirror-2909-verification.md](mirror-2909-verification.md).

## Behavior

- 2802: "busy" from a wake means admission refused the turn (e.g. a core update hold), not a live turn. It is deferred like a failed wake: retry after 5 min, doubling to 1 h, at most 8 busy wakes per row (`busy_attempts`, separate from the failed-wake budget); the last stamps busy.
- 2803: rows stamped final busy by the older core are offered again; a legacy busy row counts its old busy wake; one past the lookback is left alone.
- 2842/2843: test-only. Two backends running over one legacy final-busy row wake it once (compare-and-swap in `_claim`; busy_attempts 1); 2843 fixes comment wording.

## How it works

- `tui_gateway/notice_guarantee.py`.

## Code

- `tui_gateway/notice_guarantee.py`

## Tests

- `tests/tui_gateway/test_notice_guarantee.py`: `test_a_refused_or_missing_wake_is_final`, `test_a_busy_wake_is_deferred_and_offered_again_once_admission_reopens`, `test_busy_wakes_back_off_and_stop_after_max_busy_attempts`, `test_busy_wakes_do_not_spend_the_failed_wake_budget`, `test_a_row_stamped_busy_by_the_old_core_is_offered_again`, `test_a_legacy_busy_row_counts_its_old_busy_wake`, `test_a_legacy_busy_row_past_the_lookback_is_left_alone`, `test_two_backends_passes_over_one_legacy_busy_row_wake_it_once`.

Test files touched by these patches:

- `tests/tui_gateway/test_notice_guarantee.py`

The feature suites above landed with the source commits and were **not executed by this mirror pass**; passing claims in the source commit messages are not re-verified here. Full-series `git am` application is a structural check only.

## Applying

Apply as part of the full ordered series from the documented merge-base with `git am -3 --empty=keep` (README **Applying**). These patches depend on earlier core files and are not certified standalone. To port to a newer upstream, re-implement the Code files above and keep the listed tests green.

## Gotchas

- Re-offering depends on the 30 s backend pass; backoff constants are in the module.
- Patch numbers follow fork-log ordinals; privacy substitutions use example identities.

## Plugin?

No: these change core modules (gateway, hermes_cli, agent, state, CI scripts) that a plugin cannot hook.
