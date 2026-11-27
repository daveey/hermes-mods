# session-mentions

`cross_task` read and comment never tie a task to the calling session. They used to queue a `hermes-hub request …
shepherd`, and because a shepherded card keeps a chat "waiting", one look at a card held a finished chat open until
that card ended (2,300 such links, 131 chats on a single incident). A chat that really owns the work takes it with
`hermes-hub adopt-task`.

## Idempotent decision relays

For `cross_session(action="send", ...)`, set `dedupe_key="kanban:<task_id>:<comment_id>"`
when relaying a Kanban comment or decision. The optional nonempty string is limited to 200
characters. Omit it for ordinary repeated messages; unkeyed sends are unchanged.

Every sender session/profile on one gateway shares `session_mentions/send-dedupe.json`
under `relay_files.root()`. A permanent OS file lock serializes lookup, reservation and outbox
publication; JSON replacement is atomic. Entries expire after seven days on ledger access.
Targets normalize gateway spelling and equivalent session-link/shorthand syntax, retaining
profile and case-sensitive session IDs. Different gateway aliases are not resolved remotely.

A repeated target/key writes no outbox request. It returns `ok: true`, `delivered: false`,
`status: duplicate` and `original: {request_id, sender, sent_at, state}`. An in-flight or
uncertain first delivery returns `duplicate_pending`. The receipt is the original delivery,
not a new turn. A definite no-delivery refusal or successful unclaimed take-back releases the
key. Late courier results update the ledger even after the sender yields. An uncertain
acknowledgement retains the key: inspect the target before resending under another key.
Corrupt/unwritable ledger data fails closed. This is sender-gateway deduplication, not a
cross-gateway or receiver-side exactly-once guarantee.

Tests: `tests/test_send_dedupe.py` exercises independent senders, distinct keys/targets,
refusal/take-back/interruption retry, pending and late results, unknown acknowledgements,
unkeyed compatibility, retention, normalization, validation and concurrent processes.

## Headless Board readers

A managed headless host can opt into `hermes config set session_mentions.task_transport board_replica`.
This changes **only cross_task**: read/list use the selected board's existing authorized read-only sync
replica, without Desktop credentials or a courier. Desktop remains the default everywhere else.
Bare task ids are board-scoped (never a global first-match search); qualified references preserve the
board grant fence and require the exact replica `created_on` gateway id. Listings retain the core
`kanban_list` orchestrator gate. Comments fail closed as `read_only_transport`; there is no silent
local write or owner request. A missing replica is an error, not a fallback to the live table.
Reads return `source=replica`, owner, `live=false`, file mtime (not owner liveness), and explicit
history truncation indicators. They do not manufacture a worker capsule or live queue authority.
Cross-session transcripts and message sending still require Desktop. Tests: `test_board_tasks.py`
(entry routing regression, real SQLite read projections, target validation, owner mismatch, missing
replica and comment refusal). This mode does not install or enable the plugin by itself.

## Unreachable explicit task gateways

In the default Desktop/courier transport, an explicit task reference whose gateway
no live courier can claim and the hub replica cannot route gets one bounded,
authenticated exact-row Board lookup. The lookup uses the existing selected-board
grant gate; there is no global task-id search or gateway alias. A live canonical
row with matching board/table/uid/id and a different unambiguous `created_on`
returns `ok=false`, `rejection=canonical_reference`, and the precise
`@task:<board>@<canonical-owner>/<task-id>` to retry explicitly. This establishes
current canonical identity only, not the historical gateway binding. It neither
silently reroutes a read nor submits a comment, and exports no task body/history.
Unknown/deleted/ambiguous rows, conflicting selectors, denied boards, missing
core support and unavailable authority retain the original transport refusal.
Bare IDs, reachable couriers and opt-in `board_replica` behavior are unchanged.
Tests: `test_cross_task_hub_route.py` (retired route, isolation, ambiguous/unknown
identity, no comment submission), `test_board_tasks.py` (grant-before-network,
exact-row authority call, selected-board mismatch and connection closure).

## Failure analysis

The sender previously forwarded legacy replica keys unchanged (`locate` stripped
only the task component; `maybe_shepherd` appended it again). The installed hub
receiver's `decode_task_key` accepts exactly `profile/board/task`, so these
requests were rejected. Normalizing at the sender boundary fixes that contract
mismatch without weakening the receiver.

Why wasn't it caught? The original `test_cross_task` dispatch expectation itself
required `default/task`, and no receiver validator was exercised. The route
selection also treated a board as a preference (`filtered or hits`) and chose
the first result, hiding collisions. Tests now exercise owner profiles, strict
board selection, ambiguity and the actual installed decoder (AST-isolated to
avoid client side effects), or its explicit equivalent contract when the optional
client is absent. The original sender commit had no intervening sender fix.

## `authority_read` (courier action, no agent tool)

The coach's compute-authority check (`plugins/coach/origin_authority.py`) queues `authority_read` for a hub
Kanban worker whose card's origin chat is on another gateway. The courier pages that session's whole display
transcript (`/api/sessions/<id>/messages?order=oldest&include_compacted=true`, 500 rows a page, 20 pages at most;
longer is an error, never a partial read) and returns only each user row plus the last assistant text row before
it, slimmed (`authorityRows`, `slimAuthorityRow`). A Desktop that predates the action answers `unknown action`,
which the coach treats as no approval. Tests: `tests/authority-read.test.mjs`.

## Courier wake-up: outbox push (no tight polling)

The Desktop courier used to drain every connected gateway every 3 s (`GET /outbox`) plus a 5 s home beat, from a
worker, hidden window or not: about 70 requests/min with three connections. Now the gateway pushes and the courier
drains on the push.

Contract:

- **Event** `plugin.session-mentions.outbox`, payload `{feed, seq}` (a push), or `{feed, seq, heartbeat: true,
  pending}` (a heartbeat). It is
  a notice only and never carries requests: the courier still claims them with `GET /outbox`, so the claim rule,
  routing and home-courier gate are unchanged. `feed` is `<host>:<12 hex of the outbox root path>`. It names the
  gateway's outbox, not a process, so every serve process of a gateway and its turn-isolation child share it.
  `seq` increases per emitting process.
- **Emitters.** Every outbox write by this plugin (`cross_session`/`cross_task` in `_relay`, and `POST /enqueue`)
  calls `relay_files.notify_outbox` after the atomic write. That call never raises: in a process with no Desktop
  client (`hermes gateway run`, cron, a worker) or with no core it does nothing. A watcher thread in the process
  that serves the Desktop, started by its first `/ping` or `/outbox`, stats the outbox directory every 1 s. It
  pushes when the directory changed and holds a request, which covers writers that never emit themselves
  (user-terminal, coach, out-of-process turns). The watcher finds new requests by name from the directory
  listing. It trusts an unchanged directory stat only once the directory is 2 s old (`DIR_SETTLE_SECONDS`), because
  a temp-file create and its rename in one timestamp tick can leave the stat unchanged.
- **Lost pushes.** A push can be lost, for example in a socket reconnect. While an unclaimed request is younger than
  30 s, the watcher re-pushes every 5 s. Every 20 s it sends a heartbeat whose `pending` field says whether any
  unclaimed request waits; a heartbeat with `pending` makes the courier drain. A request is therefore drained within
  about 5 s of a lost push, inside the 15 s / 30 s pickup timeouts.
- **Stopping.** Plugin unload (`on_unload`, `__init__.stop_watcher`) stops the watcher thread. A `/ping` or
  `/outbox` doesn't restart it until the plugin registers again. The first failed push in a process is logged at
  warning, later ones at debug.
- **Feed to connection.** Each connection's own `/outbox` and `/ping` replies carry `feed`, and only those replies
  teach the Desktop which feed belongs to which connection; an event never does. The Desktop tags each event with a
  `connectionId`, but core stamps primary-socket events with the *active* connection's id, so this Mac's own gateway
  pushes arrive tagged `hub` while a hub chat is open. The tag is therefore trusted only when that connection's own
  feed equals the event's `feed`. Then only the tagged connection becomes push-live, but every connection with that
  feed is drained, so two connections to one outbox or a cloned image's twin don't miss it. Otherwise the event
  goes by `feed`: one connection gets the drain and becomes
  push-live. Several connections sharing a feed (gateways cloned from one image) are all drained, but none becomes
  push-live, so each keeps its 3 s drain instead of trusting pushes that may be another gateway's. A tag naming a
  connection whose feed isn't known yet is drained but not marked push-live. `feed` is per outbox (host + path
  hash), so the serve processes and turn-isolation child of one gateway share it. Connections that leave the connection list are pruned from
  the feed map, so a removed connection can't make a feed look shared.
- **Push-live connection.** A connection is push-live when a push or heartbeat attributed to it arrived within 60 s.
  A push makes the courier drain that connection immediately, with at most one drain in flight per connection:
  pushes that arrive during a drain fold into one more drain after it. The courier also runs a safety drain every
  60 s, and between drains it sends `/ping?courier=…` (the drain's parameters) every 8 s. That ping refreshes
  `couriers/<device>.json` and claims nothing. The 20 s courier liveness window is unchanged (session-mentions,
  user-terminal, coach), so a closed Desktop still looks gone within 20 s. A failed push-driven drain is retried
  after the 5 s error backoff, not at the next safety drain.
- **Other connections.** These are backends without the push, or connections whose pushes don't reach this window,
  for example because the window has no open socket to that gateway. They keep the 3 s drain. An event that never
  arrives is covered by that drain or by the 60 s safety drain, so the pickup timeouts (15 s / 30 s) stay valid.
- **Home beat.** `POST /result {id: home-courier}` to this Mac's own gateway now goes every 8 s (every 9 s on the
  3 s tick) with a 5 s timeout,
  instead of every 5 s. The server's home window is 20 s (`HOME_FRESH_SECONDS`), and 8 s keeps one failed beat
  plus its retry inside it.
- The tick itself stays on a dedicated worker (3 s) so a hidden, throttled window keeps its ping, home beat and
  fallback cadence. Pushes are socket events, not timers, so a hidden window gets them too.

Rate with three connections, all push-live: the 8 s ping and home beat land every 9 s on the 3 s tick, so about 3
safety drains, 20 pings and 6.7 home beats a minute, plus one drain per real request (two or three if its push was
lost and re-sent). That is about 30 a minute, down from about 72. Connections without pushes cost what they did
before.

Tests: `tests/test_outbox_push.py` covers the enqueue and tool writes emitting, an emit failure or missing core not
breaking the write (logged once at warning), the watcher pushing for an out-of-process write, re-pushing a young
unclaimed request every 5 s and not an old one, heartbeating with `pending`, listing an unsettled directory whose
stat did not change, stopping on unload until the plugin registers again, and `/ping` refreshing liveness without
claiming. `tests/outbox-push.test.mjs` covers a push draining the right connection (by feed; a `connectionId` tag only when it
agrees with that connection's own feed; a local push mis-tagged with the active hub connection still drains local;
two gateways sharing a feed; a removed connection pruned), a lost push drained within 15 s by the re-push, a `pending` heartbeat
draining, burst coalescing, the safety drain, ping and fallback cadences, retry of a failed push drain, and the `register()`
subscription. `tests/courier-liveness.test.mjs` pins both cadences inside the 20 s windows. Delivery of the event
through a running Desktop's socket is untested here.

## `@` typeahead (Desktop half)

`desktop-plugins/session-mentions/plugin.js` offers sessions and kanban tasks from every connected gateway in
the composer's `@` popover. Rows come from a per-gateway cache that refreshes in the background (60 s stale).

- **Redraw:** both completion sources pass `subscribe`; the plugin signals (coalesced over 30 ms) when a fetch
  starts, lands or fails, and core patch 0458 (`core/at-completion-refresh.md`) makes an open popover ask
  again. On a Desktop without 0458 the signal is ignored and the next keystroke picks the rows up, as before.
- **Warm start:** fetched rows are saved to `localStorage` (`hermes.session-mentions.warm.v1`, typeahead fields
  only, at most 3,000 rows per gateway and kind) and loaded on register as already stale, so the first `@` after
  a reload answers from them while a refresh runs.
- **Status rows:** a gateway with nothing cached and a fetch in flight shows `Loading sessions from <gateway>…`;
  a failed fetch shows `Couldn't load sessions|tasks from <gateway>` with the error. Picking one inserts the
  bare `@session:` / `@task:` prefix.

## `cross_task` on cards another gateway owns

A synced gateway answers `GET /api/plugins/kanban/tasks/<id>` for a card another gateway created (a Board worker
node such as worker-1, which no Desktop reaches) from its Board replica, with `source: "replica"` and `owner`
(the card's `created_on`); its own cards carry `source: "live"`, and a ready one a `queue` position. `task_read`
passes `source`, `owner` and `queue` through. A bare id keeps searching past a replica answer for a gateway that
holds the card live and uses the replica copy only when none does, so comments still land on the live holder. A
comment the gateway refuses with 409 `foreign_card` returns `rejection: "foreign_card"` and the gateway's
owner-naming message verbatim, never "not found". Tests: `tests/task-refs.test.mjs` (replica read, live wins,
foreign_card comment, `foreignCardRefusal`). The core side is daveey/hermes `plugins/kanban/dashboard/plugin_api.py`
(card t_01a1a015ef0013bd); against an older core no task has `source`, and reads behave as before.

## `cross_task` comments are recorded as an agent's

The courier posts a `task_comment` with `writer_kind: "agent"` and the sender's `writer_session` (from the
outbox request's `sender`, never from the model's args, which the agent plugin allowlists anyway). The core
endpoint records it on the `commented` event, so the running worker's live note says "from another agent
session" instead of "from the operator" (comment 179201 was stamped human). The endpoint accepts only `agent`
from a client: an explicit `human`, an unknown kind or a session without a kind is a 400; GUI comments send
neither and stay human. This is provenance, not authentication: any holder of the gateway's dashboard token
that omits `writer_kind` is still recorded human, and `writer_session` is the courier's unverified claim. A core
older than daveey/hermes card t_01a1c78af452ff15 ignores the fields (records
human, as before). Tests: `tests/task-refs.test.mjs` (posted body; args cannot pick provenance).

## Tests

From the repo root with pytest installed:

```sh
python -m pytest -q plugins/session-mentions/tests
```

`test_cross_task.py` covers read/comment dispatch, that neither queues a hub request, and list no-ops. Dispatches
are captured in memory: these tests send no real hub messages.

The Desktop half runs under Node: `node --test plugins/session-mentions/tests/*.test.mjs`.
`live-throwaway.test.mjs` covers both hide and teardown with a virtual clock: fresh snapshot revision
fences and UUIDs on PATCH/DELETE, DELETE generation fences, persistence/busy retries, and immediate
errors for upgrade/stale/unknown conflicts. A successful DELETE is followed by GET checks for ten
seconds of absence; a late close write triggers another fenced DELETE and restarts that window.
Persistent rows/guards time out after thirty seconds, and REST errors fail cleanup.
Live scripts must await `deleteThrowaway` after `session.close` in `finally`; HTTP 200 alone does
not prove cleanup because a closing runtime may persist its row again.

### Live throwaway mutation recipe

Create sessions with `source: 'scratch'`. After the first prompt persists, call `hideThrowaway`.
Before each mutation attempt GET `/api/sessions/<encoded-key>/mutation-snapshot?profile=default`.
Use a fresh UUID `request_id` and the returned `runtime_revision` as `expected_revision` in the
hide PATCH body (`hidden: true, profile: 'default'`). DELETE takes those fields in its query
string **plus** `expected_generation=runtime_generation`. Retry only explicit 409
`detail: 'session_busy'`, re-reading the snapshot first. Upgrade/stale conflicts are errors,
not write guards. Renderer hides use
`host.setPersistedSessionHidden(route,{sessionId,profile,hidden:true})` rather than raw PATCH.
The renderer recipe is documented here but is not exercised by the helper's Node tests.
`registry-names.test.mjs` covers gateway registry names: a connection whose `/ping` reports `gateway: hub` resolves `@task:default@hub/<id>` and `@session:default@hub/<id>` (unit and through a courier tick against fake backends), the drain `reach` carries it, and `hub-ec2` / `macbook` / hostname refs still resolve. `test_targeting.py` covers the gateway side: `/ping` reports `self_gateway()` (empty when the hub client is missing or broken), and a request routed to a registry name is claimed by the courier whose reach has it, not by a Desktop that lacks it.
`typeahead-refresh.test.mjs` drives `register()` and the completion sources' `provide`/`subscribe` with a fake
bridge: cold cache shows Loading then signals and offers the session; a failed session fetch and a failed task
fetch each show an error row; rows saved by one run answer the next run's first query, slimmed; corrupt or
foreign warm data is ignored. Redraw inside a running Desktop with patch 0458 is untested.

`test_worker_send_guard.py` covers the Kanban-worker send guard on both entry paths. A worker
(`HERMES_KANBAN_TASK` set, or `sender.kanban_task` on `POST /enqueue`) is refused before any outbox
write when the target source is `desktop` or `cli`, or when the source cannot be confirmed. A worker
send to a `kanban` session, and a non-worker send to a desktop chat, are still queued. The refusal
names the card to place on the target gateway and `kanban_block` with `blocker_id` when that
gateway's executor is down.

## Deployment

Update the linked plugin checkout on each sender. Fresh plugin imports use the
new code; already-running Python processes may retain cached modules. Updating
files alone does not prove hot activation. Do not restart active sessions to
force adoption; use a fresh process for corrected replay and let existing
processes reload naturally. No receiver change or replica rewrite is required.
