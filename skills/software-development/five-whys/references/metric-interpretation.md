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

For filesystem-age alerts, inspect the timestamp implementation on the running OS. On Linux, `st_ctime` is
metadata-change time, not creation time, and Git activity makes old worktrees look new; compare GNU
`stat -c '%W|%Z' <path>` (birth time versus ctime) and the creation-call history before trusting a "new
files/worktrees" count. Treat `%W = 0` as unknown, not a recent birth. Use a fixed report-time
cutoff when checking the original alert, and label later measurements separately rather than claiming they
reconstruct a changing inventory exactly.
