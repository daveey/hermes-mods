# Passive human notices preserve the preceding chat outcome

Mirror ordinals: **2124–2126**.

## Behavior

A validated human-blocked notice intentionally starts no closeout turn. If a recipient only touched the card and owns no live decision/wait, the notice should not strand that chat at `notice_tail`: its preceding turn's outcome remains visible. A real owning decision or wait keeps its existing live-work precedence.

This is not a rule that every human notice means done. A preceding request, failed turn or earlier notice keeps its own classification. The guarantee persists a restored outcome immediately rather than relying on a later read to recompute it.

## How it works

The notice guarantee first refreshes the candidate session outcome. Only the validated `human` branch whose refreshed outcome still has reason `notice_tail` stamps `outcome_informational: true` into that notice's metadata. The metadata update uses compare-and-swap on message ID and the exact previously observed metadata; a concurrent claim/change returns `skipped:changed` rather than being overwritten.

After a successful stamp, the guarantee refreshes again, deriving past the informational notice. `_completion_note` recognizes the marker only on the validated notice row shape, so outcome derivation can treat that row as informational and continue to the preceding turn. Existing live card decision/wait classification remains independent. Sessions already accounted for by a real outcome need no metadata write, preserving idle caching.

Ordinal 2124 introduced the marker and regressions; 2125 restricts stamping to refreshed stranded notice tails; 2126 explicitly asserts the restored result is stored immediately and clarifies owner/incidental-recipient semantics.

## Code

- `tui_gateway/notice_guarantee.py`: refresh-before-stamp, stranded-tail condition, metadata CAS and post-stamp refresh.
- `hermes_state_outcome.py`: informational notice recognition in `_completion_note`.
- `tests/tui_gateway/test_notice_guarantee.py`: outcome preservation, marker validation, CAS controls and immediate persistence assertions.

## Tests

Source regressions were inspected, **not executed here**. `test_touch_only_human_notice_preserves_previous_turn_outcome` is parameterized for a prior summary, request, failed turn and notice. It asserts no owning card work, no wake, preserved derived classification, unchanged tail row and, after 2126, the already-stored outcome before another manual refresh.

`test_informational_marker_only_applies_to_validated_notice_rows` varies role/display kind/metadata to reject overbroad marker recognition. `test_human_notice_stamp_does_not_overwrite_a_concurrent_claim` asserts the CAS refusal and preservation of the concurrent closeout. The adjusted tests also avoid requiring an informational stamp when a refreshed outcome is already accounted for. No live human-notice delivery or Desktop display was tested here.

## Applying

Replay the complete snapshot through [README.md](README.md), rather than taking the initial marker patch without its later idle-caching correction. Restart the backend that runs the notice guarantee and run its Python regression module in an isolated source environment. No database backfill or blanket marking of old notices is prescribed by this change.

## Gotchas

- Do not mark every notice informational; only the validated human branch with a refreshed stranded `notice_tail` qualifies.
- Preserve CAS on raw metadata. Reconstructing and unconditionally writing metadata can erase another backend's claim.
- Informational means classification skips this passive row, not that the chat or card is complete.
- Live decisions and waits remain authoritative and must not be demoted to a preceding done summary.
- This does not establish a historical cause for old unresolved chats or a general exactly-once notice guarantee.

## Could this be a plugin?

A plugin can emit validated notices, but preserving the shared outcome cache requires agreement between core notice validation, CAS persistence and outcome derivation. A postprocessing plugin that rewrites sidebar labels would not preserve the stored classification or concurrent claims.
