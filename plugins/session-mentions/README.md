# Cross-task auto-shepherd routing

`cross_task` read and comment opportunistically link a remote task to the calling
session using `hermes-hub request … shepherd`. List does not adopt tasks. Missing
replica/CLI, unknown/local tasks, and dispatch failures do not change tool output.

## Ownership and deduplication

- Replica `board/task` keys are normalized to `default/board/task` at the sender
  boundary. Current `profile/board/task` keys retain their **owner** profile.
  The `--arg profile=…` argument separately identifies the **sender** session.
- An explicit board must match the board component. A missing board match never
  falls back to another board. Multiple distinct gateway/profile/board routes
  are ambiguous and produce no request; equivalent legacy/canonical rows count
  once. Malformed routes are ignored. The receiver's strict three-part validator
  remains unchanged.
- The ledger key is `session|gateway/owner_profile/board/task`; the request ID is
  `shep-` plus the first 32 hex digits of its SHA-256. Repeated canonical requests
  are suppressed. A previously rejected two-part request has a different ledger
  key and request ID, so a corrected read/comment can queue once without deleting
  old ledger entries or reusing a rejected ID.
- The ledger records spawning, not confirmed delivery. This change is not a
  general retry/acknowledgment system. Old rejected requests and their notices
  need separate reconciliation. Do not reset the whole ledger or resend old IDs.

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

`test_cross_task.py` covers read/comment dispatch, deduplication, local/unknown
and list no-ops, and failure isolation. `test_shepherd_routes.py` covers legacy
and canonical ownership, a nondefault owner distinct from the sender, board
selection/mismatch, profile/gateway ambiguity, alias collapse, malformed routes,
and correction of rejected legacy identities. Both read and comment entrypoints
run against the receiver decoder contract. Set `HERMES_HUB_CLIENT` to a client
source file to explicitly select that validator. Dispatches are captured in
memory: these tests send no real hub messages.

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
