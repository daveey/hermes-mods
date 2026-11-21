# Gateway notices: outcome and retry custody after proven supersession

Mirror snapshot 5406; ordinals 5322, 5323, 5324.

Entries:

- **5322**: WIP: reproduce superseded notice outcome and custody gaps
- **5323**: Preserve outcome and notice custody after proven supersession
- **5324**: Keep deferred notice retries discoverable beyond lookback

## Behavior

- **5322** (work-in-progress checkpoint): adds failing tests reproducing four gaps and a first bounded candidate/projection implementation; the message itself says it was not ready to ship, and 5323 supersedes it.
- **5323**: a notice that the guarantee stamped as superseded now counts as settled for outcome derivation (previously the refresh re-persisted `notice_tail`), and discovery no longer excludes the final stamp while stranding an older unanswered notice behind it. Both use a content-bound whole-row receipt, minted only after exact local source rendering covers all content and current task/park proof establishes settlement. Legacy superseded stamps need the same proof before lazy upgrade. Mixed batches, replay wrappers, folded unrelated text and foreign/unprovable sources stay visible; appending text invalidates a receipt.
- **5324**: expiry is applied only after bounded settled-tail traversal, so a durable deferred retry hidden beneath a superseded row stays discoverable after a long quota reset. Not-yet-due retries remain excluded.

## How it works

- `hermes_state_outcome.py` derives persisted outcome and now recognises guarantee settlement.
- `tui_gateway/notice_history.py` and `tui_gateway/notice_guarantee.py` share the receipt between projection and discovery and bound the history lookup.

## Code

Shipped non-test paths (union over these entries):

- `hermes_state_outcome.py`
- `tui_gateway/notice_guarantee.py`
- `tui_gateway/notice_history.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/tui_gateway/test_notice_supersession.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- Settlement is conservative: transient source-read errors fail closed (the notice stays visible) rather than being stamped final.
- The tail traversal is bounded; extremely deep settled tails are not walked.
- Because 5322 is a WIP checkpoint, apply it only as part of the ordered series.

## Why core instead of a plugin

Outcome projection and notice discovery are core gateway/state code that every client reads; a plugin cannot change what the state layer persists as a chat's outcome.
