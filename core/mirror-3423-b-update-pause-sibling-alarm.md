# Update-pause sibling alarm: lead selection, refusal classes, ownership

Patches: **2989, 2990, 3028**.

## Behavior
- When several hosts are paused on the same update-refusal cause, a third host joins the *lead* alarm card (the oldest match that names no open sibling) rather than a follower filed in the same second.
- Refusal classes keep the head of the busy cause (the text up to its detail), so different busy causes are no longer collapsed into one class.
- A host keeps its own card under any name it carries (for example after enrolling in the Board mid-streak); a card another host created is not adopted under a hostname or pre-host key.
- Sibling notifications respect gateway ownership: a foreign pickup skips the direct write and is mirrored home once.

## How it works
- 2989 changes `find_sibling` to prefer the oldest match naming no open sibling (created_at has one-second resolution and ids are random), changes `refusal_class` to keep the cause head, adopts own cards under any host name and treats an empty `created_on` as own.
- 2990 routes the new sibling-module imports to the `e2e_upgrade` CI lane via `_UPDATE_DEPENDENCIES`, and trusts only the primary key outright in `_own_row`; hostname and pre-host keys need an empty `created_on` or one of this host's names.
- 3028 makes the sibling notification path check gateway ownership before writing directly to a board.

## Code
- `hermes_cli/update_pause_deferrals.py`
- `hermes_cli/update_pause_siblings.py`
- `scripts/ci/classify_changes.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_update_pause_deferrals.py`: `test_b_a_third_host_joins_the_lead_alarm_even_when_a_follower_sorts_first`, `test_refusal_classes_keep_the_head_of_the_busy_cause`, `test_c_another_cause_on_another_host_is_no_sibling`, `test_a_host_that_enrolls_in_the_board_mid_streak_keeps_its_card`, `test_an_own_pre_host_key_card_with_an_empty_created_on_is_kept`, `test_a_pre_host_key_card_another_host_created_is_not_adopted`, `test_an_enrolled_host_does_not_adopt_a_foreign_card_under_its_hostname_key`.
- `tests/hermes_cli/test_update_pause_sibling_ownership.py`: `test_foreign_pickup_skips_direct_write_and_home_mirrors_once`, `test_owned_and_legacy_same_board_notifications`.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
No CLI or user-facing entry path is covered by the listed tests; coverage is at module level.
