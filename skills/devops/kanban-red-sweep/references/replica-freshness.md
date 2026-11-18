# Replica freshness: what a remote-homed card's replica row can tell you

## A replica row is only as fresh as its home gateway's newest event

`kanban/sync-shadow/<board>.db` (the Board replica that `cross_task read` and the Board UI show) holds a card that
lives on another gateway as of that gateway's last push. A Mac asleep on battery runs its claimed cards only in short
maintenance DarkWakes, and its events (heartbeats included) reach the replica minutes later in a batch. So a
`running` status, an old lease or a missing heartbeat there says nothing about the run on its own.

Before you report a remote-homed run as stalled, dead or progressing:
1. Read `select max(created_at) from task_events where origin_gateway='<home>'` in the same replica. That is the
   home gateway's newest event. Your "it normally heartbeats" baseline comes from the same replica, so it is no
   check.
2. Newer than about 2 min: the replica answers for that card. Older: the replica's answer is unknown.
3. Read the home gateway's own board instead (`tailnet_exec` on that host, read-only `sqlite3 -readonly
   ~/.hermes/kanban.db`). If the host does not answer (asleep, offline), report the run as **unknown, last seen
   <time>**, never as stalled, and don't comment a stall on its card.
4. Heartbeats prove the worker is alive, not how far it has got. A run's finish (the card has left `running`) is
   the only progress proof for a "keep the Mac awake" ask (`asking-the-user` references/physical-asks.md §8).

Why: a hub chat counted 0 heartbeats for a MacBook-homed proof card in the replica, posted on its card that the run
had stalled, and had to retract it 16 min later. The Mac's own board held heartbeats from that whole window; the
replica was 25 min behind (self-heal agent-7dd79a5984).
