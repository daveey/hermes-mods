# Metric interpretation

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## 1. State the problem as an observed fact

For Hermes transcript-row counts in `state.db` ("notice stored twice", "duplicate row"), check `active`,
`compacted` and `message_uid` before hunting a second writer: in-place compaction rewinds each retained tail row
to `active=0, compacted=0` and re-inserts it as a new active row with the same `message_uid` and timestamp
(`_archive_and_compact_on_conn` in `hermes_state_messages.py`). Count visible rows (`active=1` or
`compacted=1`) per session; two visible rows are a real double insert, one visible plus superseded copies is not.
Row counts can't show a notice repeated inside one row: a user row that merges several queued notices can carry
one twice, so before calling delivery exactly-once, compare each merged row with its source notices joined in order.
Count delivery records, never subscription state, and count only after the redelivery window has closed:
- A notice admitted while its chat runs a turn waits in that chat's FIFO (`state.db` `session_admissions`, status
  `queued`). It becomes a `messages` row only when the turn ends. If you check from the target chat, a duplicate
  waits behind your own turn, so a count taken mid-turn shows one row. Count the chat's queued admissions too, or
  recount once the chat is idle.
- A notify sub can disappear after its archived card's last events are consumed (admitted to a FIFO, not
  necessarily shown), so a missing sub row is no evidence that nothing more will arrive.
- One chat can hold several subs for one card (a `tui` row and a `local` row in `kanban_notify_subs`, with separate
  cursors). Count per (card, event, chat) across all of them, over a window longer than the claim reaper's timeout
  (agent.log `held it N min without an ack`), because a reaped claim is redelivered after that timeout.

For filesystem-age alerts, inspect the timestamp implementation on the running OS. On Linux, `st_ctime` is
metadata-change time, not creation time, and Git activity makes old worktrees look new; compare GNU
`stat -c '%W|%Z' <path>` (birth time versus ctime) and the creation-call history before trusting a "new
files/worktrees" count. Treat `%W = 0` as unknown, not a recent birth. Use a fixed report-time
cutoff when checking the original alert, and label later measurements separately rather than claiming they
reconstruct a changing inventory exactly.
