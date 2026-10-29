# Replica human asks paint the chat red

Patches **0687** (`3fb26ab729`) and **0688** (`996fd25340`).

## Behavior

Under `kanban.sync.links_reader: replica`, a remote card blocked on the user paints its chat stuck (red), not waiting (grey).

- `replica_cards` decodes the replica's `human_request` column from JSON text into the dict `needs_human()` already accepts. Malformed text is no evidence, the same rule as a ledger row.
- `_replica_bucket` reads either the dict or the stored text via `_stored_needs_human`.
- A remote card in status `unknown` (done or archived, release not verified) has no worker and counts as parked. A chat whose other cards are parked paints waiting rather than no stripe.
- A remote blocked card with `human_request` set and `block_kind` NULL counts as blocked, not open.
- A lineage that mixes one local card with hub replica cards counts each once (the live shape of a local card plus remote siblings).

## How it works

Sidebar counts read `kanban_chat_links.replica_cards`. That path used to pass `human_request` through as JSON text, and `needs_human()` only accepts a dict, so every remote human ask counted as ordinary open work. Decoding happens before the bucket. The unknown-status case is a count change: `test_unverified_completion_*` expects parked 13, not 0 (the old 0 was incidental, not a rule).

## Code

`hermes_cli/kanban_chat_links.py` (`replica_cards`), `hermes_cli/kanban_session_activity.py` (`_replica_bucket`, `_stored_needs_human`).

## Tests

0687, `tests/hermes_cli/test_kanban_chat_links.py` and `tests/hermes_cli/test_kanban_session_activity.py`:

- `test_replica_human_ask_paints_the_chat_blocked`
- `test_unknown_replica_card_waits_like_a_parked_one`
- `test_unverified_completion_*` expectation moves from parked 0 to 13

0688, `tests/hermes_cli/test_kanban_chat_links_parity.py`:

- `test_remote_human_ask_with_null_block_kind_counts_blocked_not_open`
- `test_local_card_and_remote_replica_cards_in_one_lineage`

This mirror did not re-run those files. The Desktop paint (the red stripe itself) is untested; these tests cover the count inputs the stripe reads. A gateway whose replica column is already a dict, rather than JSON text, is covered only by `_stored_needs_human` accepting either shape, not by a separate replica-writer test.

## Applying

`git am -3` 0687 then 0688. Both touch the replica count path; 0688 is tests only.

## Gotchas

- Malformed `human_request` text is treated as no human ask. A card that is blocked for another reason stays in that bucket.
- Counting `unknown` as parked changes chats that previously had no stripe when their only remote cards were unverified completions.

## plugin?

No. The replica projection and the session-activity bucket are core. A Desktop plugin can read the resulting stripe; it cannot fix the count.
