# Kanban release comment proof, lock-stamp and legacy own-write

Mirror snapshot 5011; ordinals 4879, 4880, 4881, 4882, 4883, 4889, 4890.

Entries:

- **4879**: kanban release: prove legacy comment refs whose body the writer redacted
- **4880**: kanban release: match legacy comment events by uid write time; stamp rows inside the lock
- **4881**: kanban release: uid write time binds only uids minted at write time
- **4882**: test(kanban release): uid rule needs same author and UUIDv7 on both sides
- **4883**: kanban release: drop the uid write-time fallback
- **4889**: kanban release: acknowledge legacy lock-drift comments by their own integer
- **4890**: kanban release: legacy own-write binding tolerates stripped padding

## Behavior

- **Redaction** (4879): transcript proof matches a stored row against the body as written or after the writer's own forced redaction. A different body stays unproven.
- **Stamp inside the lock** (4880): `add_comment` stamps the row after the write lock, like its event.
- **Uid write-time fallback** (4881–4883): a same-author UUIDv7 write-time match was tried, then narrowed to uids minted at write time, then dropped. Adversarial review showed a sync-enable uid backfill plus an eventless same-author row could cover a foreign unsynced comment. The legacy fallback returns to the created_at rule. Kept: redaction-aware proof and in-lock stamping. New events also carry `comment_uid`.
- **Own-write binding** (4889–4890): a legacy event (no `comment_uid`, no run provenance, no transcript receipt) binds the row it names when every recorded fact agrees: row id == comment_id, author and stored length, both uids UUIDv7 within 1 s, event uid minted at its write (within 2 s of its own created_at), row stamped 0..60 s before the event. Same-second rule first. Length may differ by 0..2 stripped characters (`\n`, `\r\n`). A spare row cannot stand in without the missing comment's exact integer.

## How it works

- `hermes_cli/kanban_comment_transcript_proof.py` (`_stored_forms`).
- `hermes_cli/kanban_db_comment_ids.py` for in-lock stamps.
- `hermes_cli/kanban_release_comments.py` (`_legacy_own_write`).

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_comment_transcript_proof.py`
- `hermes_cli/kanban_db_comment_ids.py`
- `hermes_cli/kanban_release_comments.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_release_imported_comments.py`
- `tests/hermes_cli/test_kanban_release_transcript_proof.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- `--seen` release still needs a proven comment. The form without `--seen` is unchanged.
- ImportError on the redaction helper fails closed.
- Legacy lock-drift events from before the in-lock stamp stay unprovable except via the own-write integer binding.

## Plugin?

None.
