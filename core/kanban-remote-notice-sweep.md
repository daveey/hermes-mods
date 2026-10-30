# Remote notices: stale and superseded cards stop replaying

Patches:

- `patches/0969-fix-kanban-remote-notices-the-unloaded-chat-sweep-st.patch`
- `patches/0976-fix-kanban-recover-remote-notices-after-offline-gaps.patch`
- `patches/1063-fix-kanban-a-remote-card-s-notice-wakes-an-idle-chat.patch`

After a MacBook update the unloaded-chat sweep appended 293 "blocked" notices into about 216 idle chats for cards done days earlier. The hub client had switched task keys from `<board>/<id>` to `<profile>/<board>/<id>`; the frozen legacy replica rows looked live and the sweep lacked the freshness and ledger guards the live collector has.

## Behavior

- The sweep ignores superseded legacy replica rows, bounds terminal time with the freshness window and checks the delivered ledger (0969).
- 0976: owner timestamps reject notices more than six hours late and replica `updated_at` is server time, so a host baseline and immutable first-seen terminal times are persisted and shared by both consumers; terminal history is seeded on first run so offline gaps recover notices without replaying old ones.

## How it works

`tui_gateway/kanban_remote_observations.py` stores the baseline and first-seen times; `kanban_remote_notices.py` consults them.

## Code

`tui_gateway/kanban_remote_notices.py`, `tui_gateway/kanban_remote_observations.py`.

## Tests

`tests/tui_gateway/test_kanban_remote_notices.py`, `test_kanban_remote_arrival.py`, `test_kanban_remote_bootstrap.py`, `test_kanban_remote_observations.py`.

## Applying

Apply the listed patches in order with `git am -3 --empty=keep`; no config is required unless named above.

## Gotchas

None known beyond the notes above.

## Plugin

None.

## Follow-up patch (1063)

**1063** (`0bc4df24bc1`, #220): two hub cards' done notices were written into an idle MacBook chat as passive `kanban_notice` rows on time, but no turn ran until an unrelated local card woke the chat 2-3.5 h later; the remote sweep only appended the row. The production sweeper (`kanban_unloaded`) now passes a `_RemoteWaker`: a fresh remote notice gets the local sweep's grace window (the chat's live poller claims first), is then claimed in the remote ledger the way `collect()` claims it, and starts ONE turn under the same skip rules, wake budget and slots. A refused or failed wake writes the passive row (`_persist_claimed`); a turn in flight releases the claim for the next pass. Calls without a waker (tests, tools) keep the passive-row behaviour.

Code: `tui_gateway/kanban_remote_notices.py` (`_settle_claim`, `_persist_claimed`, waker path), `tui_gateway/kanban_unloaded.py`. Tests: `tests/tui_gateway/test_kanban_remote_wake.py` (4). Tests were added or changed by these commits; they were not re-run for this mirror.
