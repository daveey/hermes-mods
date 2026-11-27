# session-panel (backend half)

[daveey 2026-11-20] "precompute the status panel data on the hub, and then simply render it on the desktop ... use a websocket or some form of event streaming so that the desktop is always kept in sync without polling."

The backend half of the Desktop `session-panel` plugin (same name, so hermes-sync's desktop pairing enables it wherever the Desktop half is served and `session-panel` is in plugins-enabled.txt). The gateway precomputes the panel's data for the chats a window watches and **pushes** what changed on the gateway event stream. The Desktop renders it; switching to a watched chat needs no request.

## Contract

Urgent mitigation `HERMES_PANEL_BOARD_VISIBLE_ONLY` defaults ON. BoardLane marks
hidden sessions dirty without hydrating them; only mounted Desktop panels lease
visibility (`subscriber`, `visible_session` on `/changes`, renewed every 30s,
expires after 75s, released on unmount/window hide). Sidebar registrations are not
visibility. Attaching to a dirty cached session enqueues one immediate refresh;
the cached value may remain stale until that read publishes. `0`, `false`, `off`
or `no` restores all tracked-session BoardLane admission. This does not change
canonical reconciliation or cold sidebar population.

- `POST /api/plugins/session-panel/changes` `{sessions: [...≤200], since: rev, epoch}` registers the chats (they stay watched for 15 min after the last registration; 200 at most across all windows) and returns `{feed, profile, epoch, rev, reset, bundles: {session: bundle}}`: every bundle newer than `since`, or all of them when `epoch` is not this process's (`reset: true`). Bundles not yet computed are computed now (and pushed, so every client's revision chain stays unbroken).
- **One profile per feed.** A feed serves its process's launch profile (lineage comes from that profile's `state.db`). A request scoped to another profile (`?profile=` on a host serving several) gets `{feed, profile, profile_unsupported: true}` and no feed; the Desktop keeps its own reads for it.
- Push: `plugin.session-panel.changed` `{feed, profile, epoch, prev, rev, bundles}` via core's `hermes_cli.plugin_events.broadcast_plugin_event`, to every connected Desktop window. Every revision is pushed exactly once, in order (`prev` is the revision before the batch). Every push carries `fresh`: the chats it vouches for (held, watched, last computed without a failure). With nothing changed, a **heartbeat** `{..., prev: rev, rev, bundles: {}, heartbeat: true, fresh}` goes out every 20 s while any chat is watched: clients trust the feed only while pushes or heartbeats reach them, and only for the chats in `fresh`. A batch over 512 KB carries `sessions` (names only) and an empty `bundles`; clients fetch those with `/changes`.
- `GET /status`: `{feed, profile, running, epoch, rev, watched, bundles, failures: {session: {count, error, at}}, last_tick_s}`.

A bundle holds what the panel would otherwise fetch itself, in the same shapes:

| field | same as |
|---|---|
| `lineage` | the chat and its compression ancestors (session-tasks `lineageIds`) |
| `tasks` | `/api/session-kanban-tasks?ids=<lineage>` rows |
| `done` | `/api/plugins/user-steps/session-cards?ids=<lineage>` rows (finished cards and settled remote receipts) |
| `cards` | `{id: {detail, board}}`: a slim `/api/plugins/kanban/tasks/<id>` for every card on a linked card's dependency chain (open parents and card ids named in waits, parks and asks; six hops, 80 cards, stopping at running or finished cards): the task row with `wake_check` and `ready_since`, parents with status, the newest 40 events, the last run, the queue note, and `owner` for a replica card. Left out because they move without changing anything the chain shows: heartbeat events, `last_heartbeat_at`, and the read-time-stamped `execution_observations` of the queue note (the card rows in `tasks` keep the evidence the panel judges). A full detail is ~48 KB and ~700 ms on the hub; this is a few KB and under 5 ms. |

## Owner-controlled device waits

Core's wait classifier can expose a legacy scheduled device-access probe as
`wait_state: human` with an `owner_step`. The model renders that step in **Needs
you** (the replacement for the For-you strip), keyed by the existing card rather
than creating a decision request. Repeated probe ticks do not add asks; a current
validated human request or canonical decision takes precedence. The visible step
replaces a stale task title, without renaming the task or inventing approval
buttons. This is attention, not consent or evidence that the suggested device
operation fixes visibility. New physical prerequisites must use a validated
`human_request` and, when useful, `resolved_check`. The compatibility classifier
recognizes known paired-device probe invocation shapes, not arbitrary script
semantics.

## How it stays current

Native watch admission distinguishes source writes from inventory hints. An
unrelated temporary-file or SQLite SHM lifecycle only rearms discovery; it does
not wake either producer unless relevant source membership changed. Exact DB/WAL
writes and lost watch coverage remain conservative source hints. With keyed
hints available, the reader's derived `origin-work.db` publication is excluded
from exact watches: its cache-only changes are covered by the existing 30-second
keyed reconciliation. Older cores retain the legacy exact watch. The canonical
hub replica and origin ledger remain immediate inputs. Regression controls in
`tests/test_source_coverage.py` bound producer wakes over a virtual idle minute
using real inotify, and preserve concurrent source writes, new-board discovery,
legacy fallback and the two 30-second recovery admissions.

The feed thread starts on the first request (only `hermes serve`, which runs plugin routers and holds the Desktop sockets, ever serves one), one per process, and ticks every second until the plugin unloads (`register` stops it). `feed.py` lives under `dashboard/`, so hermes-sync's dashboard restart picks up a change to it.

1. Data stamps of every board, board replica and `state.db` (core's `session_list_flight.data_stamp`, else file mtime/size): 2 ms on the hub for 19 files. Unchanged and no full refresh due: done.
2. Cards with new `task_events` rows since the last tick, per file (`id > cursor`), with each card's chat. Heartbeat events are not changes (the full refresh renews run evidence), and card rows leave out `last_heartbeat_at`, so a heartbeat alone never re-pushes a bundle.
3. Recompute only watched chats whose bundle references a changed card or whose lineage owns one. As a safety net for changes with no event: a chat with open cards every 60 s (`FULL_EVERY_S`; their run evidence expires), one with none every 5 min (`QUIET_FULL_EVERY_S`). Measured on the hub (12 watched chats): core's `session_tasks` is ~0.35 s CPU per chat and dominates; card records, stamps and change detection are under 0.1 s for 24 chat passes.
4. Hash each bundle (without `computed_at`); push only the ones that differ, in one batch, under one new revision.

5. Nothing to push: a heartbeat when 20 s have passed since the last push.

Bundles are computed outside any lock, on per-thread read-only connections (reopened when a file was replaced, another inode, dropped when it is gone; a catch-up request thread closes its own when done). Each compute takes a sequence number before it reads, and a bundle computed earlier never replaces one computed later (a slow catch-up racing the tick). Storing, taking a revision and pushing are serialized, so revisions go out in order. One chat's failure keeps its last bundle, never stops the others, is logged (first time at warning) and counted in `/status`; a failing push is dropped (clients see no heartbeat and catch up).

Changes with no `task_events` row (a chat link adopted or released, a wake probe's next run, another card's queue move, origin receipt history) show at the next full refresh: within 60 s for a chat with open cards, 5 min for one with none.

### Bounded card-event transport

The newest-40 non-heartbeat event query retains its exact filter, tie order and
canonical `Event` conversion. With a core providing `hermes_state_page_pack`, its
bounded result crosses SQLite/Python as one typed transport cell instead of up to
40 row handoffs. This reduces GIL convoy waits under concurrent Python work;
it is not a result cache or a change to event history. Older cores retain the
original reader. Aggregate `SQLITE_TOOBIG` falls back to individual rows, since
40 individually valid events need not fit in one SQLite string; other helper
failures propagate. Uncontended transport can cost more CPU, so a component
benchmark is not a claim of whole-backend CPU acceptance.

## Urgent board-read mitigation

`HERMES_PANEL_EXTERNAL_VISIBLE_ONLY` defaults on. Board capture defers global
external-ledger reads when no panel is mounted and partitions captures only for
visible sessions. Hidden sessions retain dirty demand; attachment still hydrates
current data. Set it to `0` to capture external inputs for all watched sessions.
Canonical decision reconciliation is not visibility-gated. Its source producer
has a per-session three-second floor measured after the previous writer call
finishes. Changes within that floor retain one trailing reconciliation, due even
without another file write; they do not spend the failed-source retry budget.
A failed later capture does not roll back the actual writer's floor. Quiet
watched sessions receive a safety reconciliation every 30 seconds. Existing
per-session panel stamps, lineage checks and external-input comparisons still
select source work; the global state.db stamp remains an admission signal so
lineage and outcome changes are not silently discarded.

`HERMES_PANEL_BOARD_BATCH` is default-on (same off values) and applies only
to the serialized lane described below (`HERMES_PANEL_BOARD_COALESCE`). One
worker hydrates up to 16 chats that are due together with one reader and one
shared hydration scope, visible and demanded chats first; each chat still
publishes on its own, and all carry the read time taken before the batch's
first read (a batch whose shared reader cannot be made reads each chat with
its own reader and read time). A change admitted before a chat's turn in
the batch (the chat is still demanded, so the change makes it pending again)
defers that chat: it is not read at its turn and is read fresh when
the worker ends, with no cooldown and its original place in the queue. A
change during or after its read gets one trailing read after its cooldown;
until then the chat stays unvouched. The worker's first chat is always read.
After that, a visible chat that is due (waiting, or later in the same batch)
cuts the batch short when the next turn is a hidden chat: the unread chats
return to pending with their admission time. A stop ends the batch at the
current chat. The 30 s keyed recovery admits every watched chat, so read one
at a time they filled the lane (26 chats: 256 reads in 315 s on the MacBook
gateway). A read-only probe of those chats' linked and done rows in an idle
process cost 2.7-7.8 s one by one and under 1 s in one scope; the in-gateway
gain is not yet measured. Set the flag to `0` before a normal plugin reload to
restore one chat per worker.

`HERMES_PANEL_DUTY` is default-on (same off values). After a full feed pass
that took T seconds, the next full pass rests for 2T, bounded by
`full_every_s - 10 - 2T` so a held bundle is replaced inside its 60 s trust
window when consecutive passes are of similar length: 20 s of rest after a 10 s pass, 10 s after a 20 s pass, none from 25 s.
Keyed refreshes, a cold reader, a pending retry (including a deferred
reconcile) and the board and decision lanes are not held. Any chat, visible or
not, whose change arrives as an unkeyed source write waits for the rest: up to
25 s more, plus the 3 s floor. In exchange the feed thread no longer runs back
to back under continuous source writes while passes are short; with many
watched chats (passes of 25 s or more) nothing changes. `timing.duty_deferrals`
counts loop iterations that deferred a due pass; it stops rising while any
chat sits in the retry set (a failing chat, or rows read through the busy
fallback). Set the flag to `0` before a normal plugin reload to restore
back-to-back passes.

`HERMES_PANEL_BOARD_COALESCE` is default-on (unset or any value except
`0`, `false`, `off`, `no`, case-insensitive). BoardLane runs one hydration
worker at a time and retains each session's pending change until at least
three seconds after that session's previous read finishes. Changes during a
read or cooldown coalesce into one trailing read; the service wakes at the
cooldown deadline even without a later source event. Other eligible sessions
keep oldest-pending-first service. This deliberately trades immediate board
freshness for bounded read pressure. Set the flag to `0` before normal plugin
reload to restore the legacy four-worker, no-cooldown behavior.

## Desktop

`desktop-plugins/session-panel` runs the client (its README, "Pushed from the gateway"). A gateway without this plugin answers 404 and the Desktop keeps its own reads for it, asking again after 10 min.

## Dormant exec projection fixture

`producer_provider.py` is a panel-only exec fixture, not production registration
or an activation API. Its paired core relay only starts under pytest. A selector
preinstalled on the process-wide feed loader before API import excludes legacy
feed loading, import/reload startup and direct publication. Any retained legacy
Feed (including stopped but not proven drained), pending start or facts
registration refuses external selection rather than handing ownership over.
This is not a supported live mode switch; full broker custody and COMPLETE
admission remain prerequisites for a production bootstrap.

The serving endpoint binds `feed` and `profile` into the relay-keyed command.
The child prepares the complete wire envelope from that identity, not its own
argv; browser input cannot override it. Tests `test_exec_panel_projection.py`
exercise real exec/HTTP/stored-byte parity across two serving slots and bound
profile identities. `test_external_panel_ownership.py` covers import/reload,
legacy-custody refusal and unchanged absent-selector startup. These tests need
the paired core branch; no-core CI skips the exec module.

The parity fixture allows up to 60 seconds for broker READY, including both
execs and cold core/provider imports. This is a test-only startup allowance,
not a production activation or latency guarantee. A timeout in
`start_for_fixture` / `Channel.receive` happens before HTTP projection testing;
inspect child startup separately rather than weakening wire/identity assertions
or skipping the core-backed test. HTTP publication readiness still has its own
30-second bound and cleanup retains its 10-second bound.

## Dormant explicit-home readers

`dashboard/home_context.py` defines `PanelHomeContext`: a canonical resolved home plus opaque lifecycle and
authorization generations. It is an input from a future lifecycle controller, not an authority: constructing one
grants nothing. `CoreReaders(home_context=...)` (keyword-only; omitted keeps today's ambient behavior) and
`open_provider({'home_context': ...})` bind it at construction; browser payload fields and profile labels never
select a home and no environment variable is read or changed.

Supported with explicit paths (verified to call no ambient home/identity resolver): state.db, board and shadow
files, stamps, change detection, the live card record, watch paths, and snapshot/timing paths namespaced by
home plus both generations. Every other scope is refused by `require_scope` BEFORE any ambient resolver runs and
raises `UnsupportedHomeAdapter`, which the provider reports typed (never fresh-empty): `local_card_scope`,
`external_inventory`, `origin_history`, `panel_runtime_inputs`, `snapshot_capture`, `user_steps`,
`replica_card_owner`, `hub_db`, `mail_config`. A feed on an explicit home therefore stays unavailable until those
adapters exist. This is not projection transport, mode/roster invalidation or activation.
`tests/test_explicit_home_acquisition.py` arms tripwires on every ambient resolver of core;
`tests/test_explicit_home_context.py` covers the context, aliases, generations and the legacy control.

## Tests

```sh
uv run --no-project --python 3.14 --with pytest --with pyyaml --with fastapi --with httpx --with ruamel.yaml --with psutil python -m pytest -q -p no:cacheprovider plugins/session-panel/tests
```

`tests/test_feed.py` (bundle contents and chain walk, only changed chats pushed, unchanged recomputes not pushed, revision chain unbroken including under concurrent catch-ups and ticks, heartbeats, interest expiry and cap, failures counted and cleared, failing emit, done-history rules), `tests/test_core_readers.py` (the real readers on a board built with core's `kanban_db`: slim card record, changed cards and their chats, stamps moving on a write, a heartbeat leaving a card record unchanged, per-thread connections, a replaced board file followed, lineage, push payload JSON safety and size cap), `tests/test_routes.py` (`/changes` and `/status` through FastAPI, another profile refused). Live measurements are in the session-panel Desktop README.
