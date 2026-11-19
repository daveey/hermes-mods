# Why a sweep chat stays grey (waiting) instead of green

The sidebar colour follows what the chat still holds, not its last reply (`hermes_state_outcome._derive_current`):

- An open card linked to the chat reads as **waiting**, clock-parked (`scheduled`) cards included. A card re-parked
  for two weeks holds the chat grey for two weeks.
- A live cron the chat owns (`owner_session_id` in `cron/jobs.json`) is named on a done outcome, so the chat is
  never plain green while it owns one.
- A recorded `session_outcome` only holds until the next user or notice turn.

Seen 2026-11-14 to 11-18: the sweep chat re-parked a paused study card it was linked to until 11-27 and owned the
30-minute sweep cron. It read `waiting` from 11-14 21:04 UTC until the card was archived and the cron released.

## Rules

1. Re-parking a linked card for days (paused study, clock wait): say in the reply that this chat will read as
   waiting until that card is archived, done or handed to its real owner. If this chat only swept the card, hand it
   over rather than keep the link.
2. Once the sweep cron is verified, release it: `session_outcome {kind: done}` (default `keep_crons=false` drops
   ownership; the job keeps running).
3. The reply that reports a chain's last card also declares `session_outcome` (done, or wait on named work).

## Check

`hermes sessions outcome <session-id> --refresh --json` after the turn: `kind` is `done`, with no `on` and no
`crons`. During a turn it always reads `running`.
