# session-panel (backend half)

[daveey 2026-11-20] "precompute the status panel data on the hub, and then simply render it on the desktop ... use a websocket or some form of event streaming so that the desktop is always kept in sync without polling."

The backend half of the Desktop `session-panel` plugin (same name, so hermes-sync's desktop pairing enables it wherever the Desktop half is served and `session-panel` is in plugins-enabled.txt). The gateway precomputes the panel's data for the chats a window watches and **pushes** what changed on the gateway event stream. The Desktop renders it; switching to a watched chat needs no request.

## Contract

- `POST /api/plugins/session-panel/changes` `{sessions: [...≤200], since: rev, epoch}` registers the chats (they stay watched for 15 min after the last registration; 200 at most across all windows) and returns `{feed, profile, epoch, rev, reset, bundles: {session: bundle}}`: every bundle newer than `since`, or all of them when `epoch` is not this process's (`reset: true`). Bundles not yet computed are computed now (and pushed, so every client's revision chain stays unbroken).
- **One profile per feed.** A feed serves its process's launch profile (lineage comes from that profile's `state.db`). A request scoped to another profile (`?profile=` on a host serving several) gets `{feed, profile, profile_unsupported: true}` and no feed; the Desktop keeps its own reads for it.
- Push: `plugin.session-panel.changed` `{feed, profile, epoch, prev, rev, bundles}` via core's `hermes_cli.plugin_events.broadcast_plugin_event`, to every connected Desktop window. Every revision is pushed exactly once, in order (`prev` is the revision before the batch). With nothing changed, a **heartbeat** `{..., prev: rev, rev, bundles: {}, heartbeat: true}` goes out every 20 s while any chat is watched: clients trust the feed only while pushes or heartbeats reach them. A batch over 512 KB carries `sessions` (names only) and an empty `bundles`; clients fetch those with `/changes`.
- `GET /status`: `{feed, profile, running, epoch, rev, watched, bundles, failures: {session: {count, error, at}}, last_tick_s}`.

A bundle holds what the panel would otherwise fetch itself, in the same shapes:

| field | same as |
|---|---|
| `lineage` | the chat and its compression ancestors (session-tasks `lineageIds`) |
| `tasks` | `/api/session-kanban-tasks?ids=<lineage>` rows |
| `done` | `/api/plugins/user-steps/session-cards?ids=<lineage>` rows (finished cards and settled remote receipts) |
| `cards` | `{id: {detail, board}}`: a slim `/api/plugins/kanban/tasks/<id>` for every card on a linked card's dependency chain (open parents and card ids named in waits, parks and asks; six hops, 80 cards, stopping at running or finished cards): the task row with `wake_check` and `ready_since`, parents with status, the newest 40 events, the last run, the queue note, and `owner` for a replica card. Left out because they move without changing anything the chain shows: heartbeat events, `last_heartbeat_at`, and the read-time-stamped `execution_observations` of the queue note (the card rows in `tasks` keep the evidence the panel judges). A full detail is ~48 KB and ~700 ms on the hub; this is a few KB and under 5 ms. |

## How it stays current

The feed thread starts on the first request (only `hermes serve`, which runs plugin routers and holds the Desktop sockets, ever serves one), one per process, and ticks every second until the plugin unloads (`register` stops it). `feed.py` lives under `dashboard/`, so hermes-sync's dashboard restart picks up a change to it.

1. Data stamps of every board, board replica and `state.db` (core's `session_list_flight.data_stamp`, else file mtime/size): 2 ms on the hub for 19 files. Unchanged and no full refresh due: done.
2. Cards with new `task_events` rows since the last tick, per file (`id > cursor`), with each card's chat.
3. Recompute only watched chats whose bundle references a changed card or whose lineage owns one; every watched chat every 60 s (`FULL_EVERY_S`) as a safety net for changes with no event.
4. Hash each bundle (without `computed_at`); push only the ones that differ, in one batch, under one new revision.

5. Nothing to push: a heartbeat when 20 s have passed since the last push.

Bundles are computed outside any lock, on per-thread read-only connections (a connection is reopened when its file was replaced, another inode, and dropped when the file is gone). Storing, taking a revision and pushing are serialized, so revisions go out in order. One chat's failure keeps its last bundle, never stops the others, is logged (first time at warning) and counted in `/status`; a failing push is dropped (clients see no heartbeat and catch up).

Changes with no `task_events` row (a chat link adopted or released, a wake probe's next run, another card's queue move, origin receipt history) show at the next full refresh, within 60 s.

## Desktop

`desktop-plugins/session-panel` runs the client (its README, "Pushed from the gateway"). A gateway without this plugin answers 404 and the Desktop keeps its own reads for it, asking again after 10 min.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-panel/tests
```

`tests/test_feed.py` (bundle contents and chain walk, only changed chats pushed, unchanged recomputes not pushed, revision chain unbroken including under concurrent catch-ups and ticks, heartbeats, interest expiry and cap, failures counted and cleared, failing emit, done-history rules), `tests/test_core_readers.py` (the real readers on a board built with core's `kanban_db`: slim card record, changed cards and their chats, stamps moving on a write, a heartbeat leaving a card record unchanged, per-thread connections, a replaced board file followed, lineage, push payload JSON safety and size cap), `tests/test_routes.py` (`/changes` and `/status` through FastAPI, another profile refused). Live measurements are in the session-panel Desktop README.
