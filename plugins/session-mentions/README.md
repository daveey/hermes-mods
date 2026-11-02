# session-mentions

`cross_task` read and comment never tie a task to the calling session. They used to queue a `hermes-hub request …
shepherd`, and because a shepherded card keeps a chat "waiting", one look at a card held a finished chat open until
that card ended (2,300 such links, 131 chats on a single incident). A chat that really owns the work takes it with
`hermes-hub adopt-task`.

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
