# session-panel-feed

[daveey 2026-11-20] "precompute the status panel data on the hub, and then simply render it on the desktop ... use a websocket or some form of event streaming so that the desktop is always kept in sync without polling."

The gateway precomputes the Desktop session panel's data for the chats a window watches and **pushes** what changed on the gateway event stream. The Desktop renders it; switching to a watched chat needs no request.

## Contract

- `POST /api/plugins/session-panel-feed/changes` `{sessions: [...≤200], since: rev, epoch}` registers the chats (they stay watched for 15 min after the last registration; 200 at most across all windows) and returns `{feed, epoch, rev, reset, bundles: {session: bundle}}`: every bundle newer than `since`, or all of them when `epoch` is not this process's (`reset: true`). Bundles not yet computed are computed now.
- Push: `plugin.session-panel-feed.changed` `{feed, epoch, prev, rev, bundles}` via core's `hermes_cli.plugin_events.broadcast_plugin_event`, to every connected Desktop window. Every revision is pushed, in order (`prev` is the revision before the batch), so a client that applied every batch has no gap. A batch over 512 KB carries `sessions` (names only) and an empty `bundles`; clients fetch those with `/changes`.
- `GET /status`: `{feed, running, epoch, rev, watched, bundles}`.

A bundle holds what the panel would otherwise fetch itself, in the same shapes:

| field | same as |
|---|---|
| `lineage` | the chat and its compression ancestors (session-tasks `lineageIds`) |
| `tasks` | `/api/session-kanban-tasks?ids=<lineage>` rows |
| `done` | `/api/plugins/user-steps/session-cards?ids=<lineage>` rows (finished cards and settled remote receipts) |
| `cards` | `{id: {detail, board}}`: a slim `/api/plugins/kanban/tasks/<id>` for every card on a linked card's dependency chain (open parents and card ids named in waits, parks and asks; six hops, 80 cards, stopping at running or finished cards): the task row, wake check, parents with status, the newest 40 events, the last run, the queue note, and `owner` for a replica card. A full detail is ~48 KB and ~700 ms on the hub; this is a few KB and under 5 ms. |

## How it stays current

The feed thread starts on the first request (only `hermes serve`, which runs plugin routers and holds the Desktop sockets, ever serves one) and ticks every second:

1. Data stamps of every board, board replica and `state.db` (core's `session_list_flight.data_stamp`, else file mtime/size): 2 ms on the hub for 19 files. Unchanged and no full refresh due: done.
2. Cards with new `task_events` rows since the last tick, per file (`id > cursor`), with each card's chat.
3. Recompute only watched chats whose bundle references a changed card or whose lineage owns one; every watched chat every 60 s (`FULL_EVERY_S`) as a safety net for changes with no event.
4. Hash each bundle (without `computed_at`); push only the ones that differ, in one batch, under one new revision.

Recompute and push are serialized with catch-up requests, so revisions are pushed in order. One chat's failure keeps its last bundle and never stops the others; a failing push is dropped (clients catch up).

## Desktop

`desktop-plugins/session-panel` runs the client (see its README, "Pushed from the gateway"). A gateway without this plugin answers 404, and the Desktop keeps its own reads for it.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-panel-feed/tests
```

`tests/test_feed.py` (bundle contents and chain walk, only changed chats pushed, unchanged recomputes not pushed, revision chain unbroken, interest expiry and cap, one failing chat, failing emit, done-history rules), `tests/test_core_readers.py` (the real readers on a board built with core's `kanban_db`: slim card record, changed cards and their chats, stamps moving on a write, lineage, push payload JSON safety and size cap), `tests/test_routes.py` (`/changes` and `/status` through FastAPI). Live proof (hub CPU, push latency, Desktop switch traces) is in the session-panel README.
