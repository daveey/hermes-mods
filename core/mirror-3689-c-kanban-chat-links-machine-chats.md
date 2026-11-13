# Kanban chat links: batched machine-chat classification

Patches: **3667**.

## Behavior
- `_unlinked_chats` returns the same set as before (placeholder ids plus every session that is machine under any of its origin profiles) but classifies through `_machine_by_profile`, so several profiles take the batched path.
- `links_diff` stays clean for a missing replica whose only ledger cards are machine chats; a human chat on the same board keeps the diff unclean.

## How it works
- 3667 builds `(profile, session)` rows (placeholders filtered first) and reuses `_machine_by_profile` instead of repeating its grouping and calling `machine_sessions` per profile without prefetch. A comment in `links_diff` explains that unlinked chats are popped before `ledger_boards` is computed on purpose.

## Code
- `hermes_cli/kanban_chat_links.py`
- `tests/hermes_cli/test_kanban_chat_links_parity.py`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/hermes_cli/test_kanban_chat_links_parity.py`: `test_links_diff_a_missing_replica_with_only_machine_ledger_cards_stays_clean`.
- The batching refactor itself has no dedicated test in this patch (untested here).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- A session that is machine under any one origin profile is dropped as a whole.
