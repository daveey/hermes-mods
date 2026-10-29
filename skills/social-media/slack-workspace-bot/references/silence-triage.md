# Silent bot: no traffic, or no reply?

Run this read-only on the bot profile's owning gateway before calling a bot's silence an outage, or before parking
a card on its next real turn (e.g. to prove a routing change live).

1. **Addressed messages since T.** `slack-workspace/observed.sqlite` holds every message the bot saw, and feeds
   (`quiet_channels`, other bots) are most of it. Count only people's messages after T that address the bot: DMs
   (channel id starts with `D`) or text containing `<@BOTID>` (the bot's user id). Group the same count by day over
   the last one to two weeks: that cadence says whether silence is normal, and it is the evidence for a wait's
   `awaiting` reason.
2. **Turns since T.** In the profile's `state.db`, count `role='user'` messages after T in `source='slack'`
   sessions. Count messages rather than sessions started after T, because a reply in an older DM or thread
   continues its old session.
3. **Connection.** `logs/gateway.log` shows `Socket Mode connected` after the last gateway restart, and
   `logs/errors.log` has no Slack errors since.

Addressed messages with no matching turns, or no connect line, is an outage: fix it. No addressed messages and a
live connection is no traffic: wait for real traffic on a wake probe that counts step 2 (exit 2 when it can't read
the DB), parked per kanban-orchestrator `references/orphan-waits.md`.
