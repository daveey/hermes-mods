# fix(tests): pin event timestamps in room log page-byte bound test

Patch 0912, source `3449c1152c06bfe86128822a985e8ec3197baa9e`.

## Behavior

fix(tests): pin event timestamps in room log page-byte bound test. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def test_room_log_pages_are_bounded_by_serialized_event_bytes(tmp_path, monkeypa`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> test_room_log_pages_are_bounded_by_serialized_event_bytes flaked on main CI
> (run 40818372042, shard 1/2) with "hosted room event exceeds replay page
> limit".
>
> Problem: the test sizes MAX_LOG_PAGE_BYTES to one event's page + 1 byte, then
> reads page 2.
> Why 1: page 2's single event alone exceeded that budget.
> Why 2: event 2 serialized longer than event 1, though payloads are equal-size.
> Why 3: created_at came from time.time(); Python's shortest float repr varies in
> length (1793254574.5 vs 1793254574.123456), so events differ by a few bytes.
> Root (happened): the test assumed equal-size events but let a wall-clock value
> into the serialized size it measures.
> Root (not caught): nothing asserted the equal-size precondition, so the
> assumption only failed when the clock produced a shorter repr for event 1.
> Reproduced deterministically by giving event 1 a short-repr timestamp.
>
> Fix: pass fixed now= per event, and assert every single-event page (with more
> after it) has the same size, so drift fails loudly instead of flaking. Product
> assertions are unchanged. Verified: fixed test passes under the jittery clock
> that reproduced the CI error; file run 30x on hermes-hub with no failures.

## Code

- `tests/gateway/test_hosted_rooms.py`

## Tests

Source test files changed by this commit:

- `tests/gateway/test_hosted_rooms.py`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
