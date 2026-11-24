# Session Panel

Desktop renders the hub's **PanelModel v1** unconditionally. The hub owns work
classification, brief/option parsing, dependency traversal, wait wording and counts.
Desktop does not read raw cards, person waits or finished-card lists to reconstruct
a panel. There is no local renderer toggle.

## Model presentation and cached switching

`renderModel(model)` paints the supplied header, counts, pips and six sections:
Needs you, Stuck, In progress, Waiting, Ready and Done. Counts remain the hub's
values, even when they differ from the number of displayed rows. Details render
as plain text, never HTML or executable commands.

Models cache in memory by exact connection/profile/stored-session scope, up to
100 scopes. A route switch paints that scope's cached model synchronously while
feed registration runs separately. A missing model shows **Loading session status…**
while the focused scope triggers up to three full reads (immediate, then
250 ms and 750 ms after the preceding attempt). A registered session is not a
cached model: empty responses and transient failures still retry. A peek timeout
falls through to a full catch-up the same way a missing feed does. Reads retain already-watched
sessions at execution time before advancing the shared cursor, not unrelated sidebar chats. Requests retain
the existing 15-second bridge deadline and per-connection serialization; this is
not a five-second availability guarantee under an overloaded backend. After those
attempts, the placeholder says status is taking longer and offers **Retry**, but the
panel keeps retrying with backoff (2 s, 5 s, 10 s, then every 15 s) until the model
arrives; changing focus/unmount stops further attempts. Unsupported feeds stop automatic retries.
It never borrows another scope's model or derives a substitute from raw task fields.
A bundle without a supported model invalidates
only its own cached scope. Cached models are last-known presentation, not proof
that a worker is still running; heartbeats never recompute their work states.

## Request-backed decisions

Request-backed asks require a canonical request ID, exact revision, decision
owner and choice ID. Unbound non-card options are disabled. Until the owner-side
remote courier cutover, unmirrored card choices preserve the existing explicit
composer draft: choosing does not send or record the decision, and the hint says
to send it in the chat. A changed focused route refuses the stale draft.

Choices use the existing user-steps decisions POST. On click, Desktop reads the
request on a candidate connection and verifies its exact gateway/profile/stored
session owner, revision, open state, choice and complete scope before posting.
The idempotency key and intent persist **before** POST. Delivery is established
with operation lookup and receipt readback, not the POST response alone.
An unknown outcome locks all options and offers **Check status**, retaining the
same key across reloads. Confirmed decisions remain locked against old cached
models. Choices requiring free text, withdrawal and delivery Check status/Retry
remain available in user-steps' compact companion queue without duplicate tiles.
Open card uses the existing owner-qualified session-tasks connection mapping.

## Feed transport

Desktop registers the focused chat, recent chats and active sidebar chats with
`POST /api/plugins/session-panel/changes`, grouped by connection/profile. The
backend pushes `plugin.session-panel.changed` models. A 60-second catch-up is the
safety net; visible-window recovery re-registers immediately. Recent route IDs
persist locally, but raw card and dependency-read caches are no longer maintained.

The transport preserves ordered revisions, duplicate suppression, gap/name-only
batch catch-up, epoch reset, profile aliases, and serialized/coalesced catch-ups.
Older catch-up answers cannot overwrite a newer live push. Restored backend
revision jumps catch up without changing epoch; an older backend snapshot resets
only when pushes no longer reach Desktop. Unsupported backends/profiles back off
for ten minutes and leave the missing-model state visible, without local raw reads.
`feedIsLive` and `feedVouches` report transport freshness only.

## Tests

```sh
HERMES_DESKTOP_DIR=/path/to/desktop-deps \
  node --test --test-concurrency=1 desktop-plugins/session-panel/test/*.test.mjs
```

The dependency directory needs React, React DOM and jsdom (the public Desktop CI
workflow pins versions). `test/hub-model.test.mjs` invokes the real Python reducer
through `test/hub-model-fixtures.py` for public structural goldens, then exercises
DOM presentation, cached/missing scopes, scoped draft fallback, disabled unbound questions, verified
owner/revision routing, canonical POST/readback and unknown-outcome locking.
`test/feed.test.mjs` covers model caching, raw-field isolation, transport recovery,
revision ordering, cache bounds and coalesced registrations. Removed local UI and
inference suites are not retained as exports solely for tests.

Optional `HERMES_PANEL_GOLDENS=/private/path/private-golden-cases.json` adds private
preserved model goldens; never commit those inputs. Schema:
[`../../plugins/session-panel/MODEL.md`](../../plugins/session-panel/MODEL.md).
Native Desktop activation, live backend integration and screenshots are separate
validation gates; Node fixtures alone do not establish deployment.
