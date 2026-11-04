# Kanban: one open hermes-mods core mirror card

Patches: **1720** (`9da750e43b`, `[daveey] Enforce singleton ownership for hermes-mods core mirror passes`); **1772** (`d1f73dbf8a`, `[daveey] Widen mirror singleton identity and preserve capability execution`); **1801** (`8dc2012416`, `fix(kanban): hermes-sync bug cards are not the core-mirror singleton [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- `kanban_create` refuses a second open card that is a hermes-mods core mirror pass and names the open owner (1720); the singleton identity is widened while capability execution cards are preserved (1772).
- A hermes-sync bug card that mentions its core pass is not a mirror card: `core` and `fork backlog` count only when the title lacks the hermes-sync token; `hermes-mods` still matches alone (1801).

## How it works

`hermes_cli/kanban_overlap.py` adds the singleton rule beside the word-overlap rule; entry-parity tests cover each create path.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1720: (subject only)
- 1772: (subject only)
- 1801: The widened mirror singleton matched hub t_01a19ddcef566978, a hermes-sync bug card naming its core pass and a missing mirror checkout, so it was refused while any mirror card was open. 'core' and 'fork backlog' now count only when the title lacks the hermes-sync token; hermes-mods still matches alone. 30-day hub replay (live board, other boards, replica; 7314 titles): 74 singleton matches on main, 73 after, the only drop being that card. t_01a19ea9f1739df6

## Code

`hermes_cli/kanban_overlap.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_create_entry_parity.py`: `test_mirror_singleton_refused_on_every_create_path`
- `tests/tools/test_kanban_create_overlap_singleton.py`: `test_capability_owner_serves_waiter_but_not_other_mirror`, `test_closed_mirror_not_an_owner`, `test_exact_mirror_pairs_refused`, `test_hermes_sync_bug_card_is_not_the_mirror_singleton`, `test_historical_mirror_variants`, `test_mirror_override_is_recorded`, `test_mirror_same_family_and_same_chat_still_refused`, `test_non_mirror_titles_do_not_match_singleton`, `test_real_non_mirror_identities`, `test_singleton_does_not_change_word_score`, `test_singleton_is_not_a_heuristic_fanout_or_target_match`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- Lineage, waits-on and an explicit citation of the owner still allow a related card.

## Plugin?

No: these change core modules that plugins cannot hook.
