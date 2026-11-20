# Turn-observer outage alarms

Mirror entries: 4481, 4482.

## Behavior

- **4481** — Gateway maintenance detects enabled turn-observer outages after an inclusive ten-minute grace and reuses a host repair incident.
- **4482** — Malformed observation clocks and structurally invalid owner publications cannot reset outage grace.

## How it works

Only the Linux default account home, default primary board and actually enabled observer timer qualify. The observer publishes status but does not own alarm writes; a nonblocking host-level lock prevents concurrent maintenance processes duplicating the account alarm.

The reader bounds JSON input size and validates version, board, source revision, owner structure and finite clock ordering. A healthy empty owner list differs from missing or invalid state. A structurally valid publication can prove an earlier success even if a status-write crash occurred; an invalid publication's timestamp cannot. Owner expiry must cover the publication validity interval.

After 600 seconds of unsuccessful observation, one repair card is created or reused. Repeats are hourly; recovery adds a comment, and a later outage can reuse an open card. Closed incidents get new cards. The detector persists first-seen state and heals interrupted create/event bookkeeping without modifying admission scoring.

## Code

- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/turn_urgency_status_alarm.py`

## Tests

Source alarm tests cover fractional exact boundaries, missing/corrupt/expired state, malformed clocks, initial grace persistence, cache/status and card/event crash windows, repeat/recovery cycles, closed incidents and default-home/board/enablement exclusions. No live timer outage or installed service proof was executed for this documentation pass.

**Evidence boundary:** normalized patch bodies and their source tests were inspected for this document. Source tests and historical validation statements are not freshly executed verification results.

- `tests/hermes_cli/test_turn_urgency_status_alarm.py`

## Applying

Use the complete segmented series recipe in [README — Applying](README.md#applying), including its ordered integration bridges and required post-replay repair/verification steps. These entries share context with the surrounding mirror and are **not independent patches**. Do not apply just the ordinals listed here as a standalone feature install.

## Gotchas

A unit file alone is not enablement. Named profiles must not duplicate the account-level alarm. This feature reports source health; it does not grant a turn, change priority math or justify restarting deliberately contained timers.
