# PanelModel v1

## Overdue triage: operational red, not a human decision

`hermes_cli.kanban_triage_attention` is the authoritative backend rule: a current
triage episode is red only after **strictly more than 600 seconds**. At 599/600
seconds age does not escalate; at 601 seconds or six days it does. Human asks may
already be red earlier. This explicitly supersedes red-only-for-human policy.

Keep `needs_you` human-only and preserve actor wording and all spend, scope,
containment and execution restrictions. Red authorizes no lifecycle changes.
Overdue rows/groups use `attention: "triage_overdue"` in `stuck`; their box, pip,
header attention overlay and session-tasks chip are red. Canonical status remains
intact and its actor/reason remains in the header label alongside overdue triage.

Adapters detach `triage_entered_at` from full relevant lifecycle events **before**
clipping the display tail. Creation counts only if it entered triage; comments,
heartbeats and repeated holds do not reset the episode. Exit clears it; re-entry
starts fresh. Missing/invalid/future/stale source time remains unknown, never a
`created_at` estimate. Local, replica and origin-ledger paths carry this scalar.
Older cores without the helper retain unknown age rather than a duplicate rule.

The feed schedules bounded next-threshold invalidation (`triage_deadlines`) for
listed rows and shared-blocker groups, keyed by the reducer's own serving-gateway
identity (`serving_row`); a crossing the feed already re-reduced never re-arms the
wake, so after the age pass the loop returns to its normal idle cadence. Core
detached annotation snapshots project distinct `kanban_triage_attention` counts and qualified refs.
Canonical fanout has a separate successful-clock deadline, refreshing stored
outcomes without task writes or reopening chat. Ordinary/Background counters
consume the operational field; session dot/stripe consume the refreshed canonical
red reason. Display-only elapsed ticks do not reread histories, poll HTTP or churn
model revisions. Expired observations cannot establish current overdue attention:
stale snapshots drop `triage_overdue` from rows, header label/attention and pips
(the row keeps only explicit "last known:" text).

Tests cover strict boundaries, six-day age, current episodes, repeated holds,
exit/re-entry, unknown times, actual human-request controls, local/replica inputs,
fake-clock unchanged-board canonical crossing/recovery, real CoreReaders → reducer
→ mounted panel/chip with clipped event history, and actual ordinary/Background
sidebar dot/stripe/counters. Native pixels remain a separate passive observation.

## Delivery wait ETA

Rows may carry `wait_kind: "ci" | "deploy" | "clock"` and
`wait_eta: {waited_since: number | null, expected_s: number | null,
estimate_source: string | null}`. Times are Unix seconds and durations in seconds,
passed through from the producer without estimating in the reducer. The waiting
header and section pip may also use these kinds: `ci`/`clock` paint light blue (`#38bdf8`),
`deploy` (merged work waiting for a hub deploy) paints purple (`#a371f7`).
Non-delivery rows omit the fields.

Desktop replaces a delivery row's pip with right-aligned `waited / expected  left`
columns when a park timestamp is present. Its one-second display clock updates
the readout without network reads or model revisions. Missing estimates show
`?` and `—`; overruns show `+duration` in `#f0883e`. Hover text names the
producer's estimate source. Older payloads lacking a park timestamp retain pips.
Double-clicking the readout retains the owner-qualified Open card action.

## Relative display text

The feed calls `reduce_panel(..., wire=True)`. A row (or its `decision`)
may carry additive `clock_text` templates for `detail`, `details`, or `text`.
Each template is an array of literal strings and objects
`{timestamp: <absolute Unix seconds>, format: "ago" | "elapsed" | "remaining"}`;
`details` is an array of templates. No current/read clock or formatted age is
part of these templates. The existing string fields remain static fallbacks
for older clients. Internal construction tokens never leave the reducer.

Desktop inserts relative text from its local clock and repaints locally;
the cached model, revision and network cursor are unchanged. Session-tasks
uses the panel's shared `clockRow` bridge when available. Missing/older
renderers retain static wording rather than fabricating execution freshness.
Classification, evidence expiry, quiet/stuck thresholds, deadline crossing
and decision-delivery state remain server-owned semantic changes. The
default non-wire reducer retains timestamp-pinned textual snapshots for
existing offline consumers; every feed publication path uses wire mode.

`POST /api/plugins/session-panel/changes` and `plugin.session-panel.changed` keep
all existing bundle fields and add `bundle.model`. P1 does not change Desktop.
The model is a presentation snapshot, not authorization or execution authority.

## Unactivated snapshot acquisition capability

[BOARD_SNAPSHOT.md](BOARD_SNAPSHOT.md) documents the memory-only `board-snapshot/1`
store, scope-certified canonical acquisition seams and strict pure readers. By
default this capability has no production consumer, scheduler or activation. It does
not replace the active freshness behavior below. Consumer integration must preserve
canonical decisions and explicitly merge unknown source parts; strict snapshot
readers raise on missing coverage rather than attest empty. No live latency or CPU
acceptance follows from acquisition-only tests.

## Unactivated reserved source composition

[SOURCE_COORDINATOR.md](SOURCE_COORDINATOR.md) documents an explicitly composed
coordinator which reallocates the existing four work slots to one local source,
one strict external source and two pure projections. Once explicitly composed,
board/compute/full/tick/catch-up share B1 acquisition and B2a immutable projection
and publication fences. No deployed startup or handler composes it: the default
runtime below is unchanged. Canonical requests/reconciliation remain independently
owned and ordered; neither snapshot success nor a newer computation sequence is
consent. Missing source coverage remains unknown and excluded from freshness.
[SOURCE_LIFETIME.md](SOURCE_LIFETIME.md) describes genuine predecessor/reload and
current reader custody; [SOURCE_RECEIPTS.md](SOURCE_RECEIPTS.md) describes exact
stored payload, post-transform server emission and authenticated ASGI response
write custody. [SOURCE_ACTIVATION.md](SOURCE_ACTIVATION.md) defines B3's activation,
drain/window and rollback receipt hooks. All remain explicitly UNACTIVATED.
Final lifecycle/population acceptance and B3 activation/performance/native proof
are required, not implied by source or cached hashes. No wire version or Desktop
classification changes.

## Non-running task reasons

An active chat is labelled `chat active · task execution shown below`; it does not
assert that a linked task's worker is executing. The canonical header state and
execution identity remain unchanged. Task rows retain their own evidence and the
panel's independent source-data age.

Ready tasks may carry an optional source-qualified `non_running_reason`. A current
refusal is shown as blocked with its fixed cause, evidence freshness and next
actor/action; an actual capacity or observer-local drain/start gate stays waiting.
Missing, stale, replaced, conflicting or truncated evidence says `execution reason
unknown`, never an invented capacity wait or human request. Diagnostic expiry is
independent of execution phase/identity and never renews a worker lease.

Native schedule, human, startup and review lifecycles remain authoritative only
for their source snapshot. When that snapshot expires in a cached renderer, native
task rows become historical `status unknown · last known: …`, withdraw stuck pips,
wake/ETA colouring and task-decision actions, and retain read-only navigation.
A fresh source restores the current native wait. This never clears a stored hold.
The self-contained `test/nonrunning.test.mjs` harness runs real disposable core
producers through the reducer and mounts the actual renderer; these tests are not
proof of native on-screen pixels or a deployed gateway.

## Runtime process evidence

A process row with complete owner coordinates exposes **Open process** in its
right-click menu. This opens a read-only Desktop dialog for that process, not
its parent chat. The gateway/profile route is frozen at click time; navigation
closes the dialog and late responses cannot replace another owner's view.
The dialog retains the backend-authored panel observation explicitly labelled
“at open”, then uses existing owner-scoped `session.resume` (lazy, no messages)
and `process.list` RPCs for status/output. Refresh is explicit, never polling.
Foreign checkpoints can be visible to the canonical evidence reader but absent
from a backend's in-memory process list; in that case output availability fails
explicitly while the labelled panel observation remains visible. It never
re-routes to the focused backend, adopts a process, acknowledges its log,
executes a command or offers process control. Missing owner data exposes no
action. Older Desktop builds without scoped requests/dialog support report the
capability gap rather than opening another process.

The background canonical lane watches the profile's `processes.json` for process
birth/exit and re-probes known open processes every 45 seconds. This renews actual
kernel observations, not a carried payload's timestamp or the board snapshot's
`computed_at`. The admitted read invalidates the process adapter's short sidebar
cache so a birth immediately following an empty read cannot be certified absent.
Probe failures retain old evidence with finite retry, never a new running lease.
When no process remains, timed process probes stop. HTTP readers do no source I/O.
Scheduling is per chat: a chat is re-captured when its own fingerprint moves (the
`processes.json` fields core's process inventory reads, for the entries core attributes
to its lineage; `inputs.PROCESS_CHECKPOINT_FIELDS`), on its own 45-second re-probe while
it holds processes, or on its own retry clock (1 s after losing a publication race; a
failed capture backs off from 0.1 s to at most 45 s). A rewrite that leaves its evidence
unchanged, another chat's process, or another chat's failure never makes it due.
The fingerprint covers sandbox-scope entries too, and the union of the panel lineage and
core's compression lineage of the chat's tip (what refresh_outcome reads); every profile's
checkpoint is watched. A capture invalidates core's 3-second process cache before
refresh_outcome, so an exit that made the chat due is never certified from the warm cache.
The refreshed canonical `outcome_json.status` remains the one state envelope shared
by the panel header and Desktop sidebar; rows do not implement a competing status
classifier. Intentional process termination can leave a failed receipt requiring
acknowledgement rather than automatically claiming successful completion.

## Board-source freshness

Wire models carry additive `freshness: {trusted, computed_at, ttl_s}` transport
metadata. It is outside the reducer's semantic revision: `computed_at` is the
actual board snapshot clock, never a decision refresh or heartbeat receipt.
The same source-trust predicate supplies heartbeat `fresh` membership. Restored
and carried bundles are untrusted until recomputed. Desktop keeps the header's
last-known backend label with `· synced <age> ago` when its source clock is
finite, positive and not in the future, including heartbeat exclusion and TTL
expiry. This is historical text only: effective state is unknown and the stripe
stays neutral; no execution trust, action or model revision is renewed. Missing
state, missing/invalid clocks and future clocks retain the unknown fallback.
A live transport cannot revive old running evidence.
The local display timer may expire trust, never infer a new execution state.
`/status` exposes `oldest_bundle_age_s` and owner-session-keyed
`visible_chats: {session: {age_s, computed_at}}`; missing bundles have null age.
Mounted panels outrank older FIFO demand; chats named by `/changes` gain a
bounded demand lease and can rehydrate dirty/restored/expired bundles even when
not mounted. HTTP still only enqueues demand and reads immutable bytes.

The **unactivated**, opt-in [board projection API](BOARD_PROJECTION.md) adds a pure
partial consumer and exact source-vector/ScopeDemand publication CAS over the
strict acquisition store. It starts no service and changes none of the production
routes described below. Partial unknown sources remain explicit diagnostics and
unserved acquisition custody, never certified empty input. Immutable shared
materialization is selected without per-chat SQLite/network I/O; canonical
requests/errors merge independently from board data. A later `computed_seq` is
not authority to overwrite a newer source vector. PanelModel stays version 1;
classification and consent remain backend-owned. Production cutover, reserved
roles and live/contention/component acceptance remain separate obligations.

A separate change-driven board lane observes the canonical task-change index and
external hub/origin-ledger/replica source stamps, and publishes affected watched
chats independently of full-sweep mirror reconciliation. External captures use the
canonical owner-qualified adapter and semantic comparison; sync-bookkeeping-only
pulls carry observations without hydration or renewed computation/execution clocks.
Known local task-change deltas enter the pending queue and can publish **before**
the same pass starts strict external capture. A later changed external snapshot
admits its own successor, including for a chat already locally admitted; the first
read may predate that external input. Capture failure leaves the source cursor and
external baseline uncommitted for finite retry. Capture-failure custody is separate
from per-chat hydration failures: neither an earlier local read finishing later nor
a full hydration/heartbeat can vouch for held chats until successful strict recapture.
This fence survives in-process Feed resume and is removed when membership is forgotten;
recapture clears only its own fence, not pending or failed hydration/decision work.
This does not make a local event
arriving during an already-blocked capture independently observable: the capture
coordinator remains shared, and that pre-admission interval must still be measured.
Four bounded workers hydrate one chat each; a blocked reader does not hold the
coordinator or another chat's publication. A separate event-driven capacity service
drains completions and refills admitted slots even while the coordinator is inside
strict external capture. Registration by the Feed's capture-owner thread establishes
that service even when natural reload runs only a saved predecessor entry, with no
current API call. Current API entry also idempotently ensures service on an existing
lane, without duplicating its capture thread or resetting source cursors. Manually
driven lanes do not implicitly start service. It performs no source reads and shares the scheduler lock;
the coordinator remains the single source-cursor owner. Pending admissions are oldest-first;
coalescing a newer write retains a chat's oldest unserved admission. Writes during
an in-flight read coalesce
into one subsequent read. Source cursors are separate from the full sweep and only
commit after complete capture/admission. Failed captures and stale reads have finite
backoff, then await a real source write; there is no idle database polling.

Board-only reads retain the held canonical outcome/request component and its
`canonical_seq`, and never invoke mirror reconciliation or canonical request reads.
They share `computed_seq` ordering with sweeps, so a slow older sweep cannot replace
a newer task snapshot. Membership tokens reject forgotten/re-watched work; lineage
changes require full hydration rather than reusing another lineage's decisions.
Readers own and close their connections per invocation. `timing.board_recent`
records admission, first read, publication, per-reader durations and whether a
publication changed the model; `producer_readers` records per-chat reconciliation.
`timing.board_captures` retains local/external admission wall times, strict external
capture duration and failures. Successful `timing.recent` push records include each
emitted model's exact revision and board computed sequence/time; failed transport
calls do not create successful emit records. These bounded records distinguish
capture/admission and emission from sparse disk checkpoint observations.
These are producer timings, not installed Desktop click-to-render measurements.

## Historical parked board status

When a board snapshot is over 60 seconds old and a parked card has only a
producer gap (for example no run identity), retain its last board-derived parked
or ready detail with `as of <Pacific time> (panel data <elapsed> old)`. The age
uses `clock_text` in wire mode. Execution evidence remains unknown; omit live
producer `wait_kind`/ETA signals. Fresh snapshots retain their existing text.
Real execution contradictions, unknown board states, future read clocks and
explicit stale/partial owner reads continue to show unknown. Running cards never
use this historical parked fallback. Session-tasks copies the same model detail.

## Canonical decision freshness

A separate backend lane reads canonical request lifecycle/resolution and session outcome
once per second. It uses the same verified owner/profile request views, without mirror
reconciliation or board hydration. Full sweeps still reconcile mirrors and refresh all
execution, dependency and inventory inputs. This prevents an unrelated slow board read
from hiding the entire answered-but-not-delivered interval.

`canonical_seq` orders request/outcome reads independently of `computed_seq` (the board
snapshot). Publication merges each newest component, rejects a fast read after interest
was forgotten or lineage changed, and preserves `computed_at`. A decision refresh never
renews an execution lease, restored/carried board trust or board-source freshness. On a
merge the reducer evaluates execution expiry at the current clock, not the old board
clock; unchanged canonical inputs do not re-reduce or issue revisions every second.
Failed fast reads are retried on the next pass and excluded from heartbeat `fresh`;
`/status.decision_failures` exposes the affected sessions. Real delivery failures and
unknown operations retain user-steps Check status/Retry recovery.

Remote card-answer retirement is nonterminal delivery custody, even though its stored
request is `cancelled` to refuse new proofs. The user-steps presenter supplies
`delivery={state: "delivering", text, ageSeconds, unconfirmed}`. The reducer uses
that text as the row's single `decision` status line (no choices or Retry), retaining
a standalone Waiting row if the card departed. It never labels this state request
cancelled. The standalone and hub-companion decision queues keep reading the exact
operation until a delivered receipt or terminal retirement clears custody; the panel
owns the line when mounted, rather than drawing it twice.

The API idempotently ensures the canonical lane even if a previous API generation
already resumed the current Feed. That lane owns current-generation readers rather
than borrowing obsolete readers from the board lane. A successfully queried missing
session row (a deleted chat retained in Desktop recents) yields an empty/unknown
outcome and no requests: user-steps cannot establish that session's request owner
without its own stored row, so the adapter must not call its guarded panel_requests
or infer an ancestor's owner. Existing sessions still read exact owner/lineage requests.
Unavailable databases or canonical-request readers remain retryable failures, not
known-empty sources; successful retries clear failure diagnostics and restore `fresh`.

## Interactive decision publication

Canonical owner lookup, decision POST, and operation/receipt readback use a bounded
four-worker owner-route budget separate from the shared AnyIO and feed read pools.
The router's recovery dependency is asynchronous too: warm decisions do not wait
for a synchronous dependency in the saturated default pool. Queue admission is
bounded at 750 ms; an admitted worker retains capacity even after its caller is
cancelled. Cold callers await one shielded recovery task per home/event loop, not
four workers waiting on the same recovery lock. A definitive pre-admission 503
(`Decision not started: capacity busy`) offers an explicit Retry decision with
the identical frozen payload and idempotency key. Generic 503/timeouts stay
unknown and can only check status; a failed GET never grants POST retry.
Durable operation/idempotency semantics are unchanged.

Desktop validates the canonical POST receipt immediately instead of discarding it
and doing three sequential GETs. A complete owner/request/revision/idempotency-bound
receipt with a recorded/pending or delivered operation creates a temporary, local
presentation keyed by operation ID: that exact ask moves from Needs you to Waiting,
its count/pip changes, and its single line says the decision was recorded. It does
not alter the cached backend model, canonical status, execution trust or leases,
and does not imply that work is running or grant any authority. The header may
say `Decision recorded` until canonical replacement. Failed/unknown POSTs never
create this presentation. A failed operation/readback or unavailable refresh
restores the locked ask with Check status; the frozen intent/key is retained.
The scoped canonical refresh runs in the background, replacing the temporary
presentation whether it confirms departure or returns a still-open ask. Other
owners, source chats and request revisions remain untouched.

Client telemetry observes a mounted React commit, not GPU presentation. Optional
`visible_ms` is click-to-provisional-commit; `painted_ms`/logged `total_ms` measure
the first observed departure (provisional or canonical). `refreshed_ms` remains
independent and may finish later. A single best-effort report waits until both
refresh completion and a committed departure have been observed; an unavailable
canonical model alone is never departure evidence. Older clients retain their
ordered responded → refreshed → painted offsets.

Desktop requests `POST /decision-refresh {session}`
on the **source panel** connection/profile, not an ambient or foreign request
owner. This endpoint reads current canonical requests/outcome only, independent
of the sweep decision lock, and returns a compact backend-authored model. It
uses its own separately bounded four-worker refresh capacity (not the owner's
four slots). Failed interactive reads arm the existing bounded canonical retry
budget and stay unvouched until a successful canonical read. It does
not hydrate boards, reconcile mirrors, infer running state, renew execution
leases, or advance board clocks. The existing canonical sequence merge fences
older reads. The scoped response carries feed/epoch/revision; per-chat revision
fences prevent an older push/catch-up from resurrecting an answered ask without
advancing the connection-wide cursor and dropping unrelated updates.

An unavailable refresh retains the recorded receipt and existing locked intent;
it never repeats the decision. Older owners without a complete POST receipt use
the existing exact-key receipt recovery. Native click-to-paint timing and transport
contention must be measured separately from in-process saturation regression tests.

## Header fallback

A validated published `outcome_json.status` remains authoritative, unchanged, with
`header.source="canonical"`. Otherwise `header.status` is null and `source="derived"`:
the hub derives presentation from sections, in priority order `needs-you`, `stuck`,
`running`, `waiting`, then `idle`. Ready/not-started cards count as waiting, not
settled. Empty readable bundles and done-only history are idle. Legacy outcome
kinds are never accepted as the status protocol. Row-source `input_errors` retain
`unknown`; an unavailable session outcome alone does not make readable rows unknown.
This fallback neither asserts canonical coverage nor authorizes execution or green.
Desktop renders the backend state/label only for canonical or explicitly derived
headers; older unmarked missing-status models retain the conservative unknown label.

Canonical `unresolved` is not synonymous with interruption: the backend labels
`reason=source_unknown` as unavailable status, and only `reason=interrupted` as
interrupted. Retained stale/partial card rows carry `evidence_state=unknown`, remain
Waiting, identify the observation owner and do not request user action. The core
adapter must preserve these availability flags before interpreting status or asks;
unknown evidence cannot establish running or completion. Independent own-turn
activity can justify a running header while remote rows remain unknown: the header
then explicitly says this chat’s agent is replying. Source health recovery is a
canonical-outcome invalidation, not permission for Desktop to reclassify the header.

## Dependency outline (additive v1)

Counted rows remain in their original section arrays, with optional `parent`
(another node's owner-qualified key, or null) and `depth` (0–4). The pure reducer
uses the same hydrated next-hop resolver as the blocking-card label: explicit
primary producers win, canonical clock/CI/deploy waits are never replaced with
graph ancestry, and no additional I/O occurs. Waiting, stuck, ready and needs-you
cards attach to the nearest displayed dependency in any section. Walks are bounded
to 80 visits; cycles and inconclusive bounded walks remain flat. Overflow at depth
five starts a new root rather than hiding a row or inventing a shortcut edge.

`groups` is an optional top-level array of non-counted `kind="blocker"` row nodes
for at least two dependents sharing the nearest off-list blocker. They carry its
hydrated title, status, gateway/location, exact open-card action and backend-authored
state-square `section`. Their key is `blocker:` plus the owner-qualified card key.
A single off-list dependent stays flat. Groups appear at the first child's section;
cross-section children retain their own state squares and header counts/pips.

New Desktop clients render each root and its children once, with indents and tree
guides, moving children under displayed running/stuck parents. Panel collapse hides
whole trees. Session-tasks chips may stay flat. The fallback `detail` is unchanged
for old Desktop and flat consumers. Optional `nested_detail` suppresses only an
exact repeated parent label in the nested view; other blockers, status, warnings,
reasons and ages remain. `clock_text.nested_detail` uses the existing local-clock
template contract. Missing parent fields render flat. Parent and depth are backend
presentation, never execution/decision authority; ask bindings stay unchanged.

## Contract

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "PanelModel v1",
  "type": "object",
  "required": ["version", "revision", "header", "sections", "diagnostics"],
  "properties": {
    "version": {"const": 1},
    "revision": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    "diagnostics": {"type": "array", "items": {"type": "string"}},
    "groups": {"$ref": "#/$defs/rows"},
    "header": {
      "type": "object", "required": ["state", "status", "source", "counts", "pips"],
      "properties": {
        "source": {"enum": ["canonical", "derived"]},
        "state": {"type": "string"},
        "status": {"type": ["object", "null"], "description": "Validated core status_version=1 envelope, preserved with identity, revision, generation, coverage and presentation."},
        "wait_kind": {"enum": ["ci", "deploy", "clock", "card", "agent"], "description": "Set when every waiting row has known custody: producer waits paint light blue; agent decisions paint blue. Mixed agent/producer waits use agent blue."},
        "counts": {
          "type": "object",
          "required": ["needs_you", "stuck", "running", "waiting", "ready", "done"],
          "additionalProperties": {"type": "integer", "minimum": 0}
        },
        "pips": {"type": "array", "items": {
          "type": "object", "required": ["section", "count", "label"],
          "properties": {
            "section": {"enum": ["needs_you", "stuck", "running", "waiting", "ready", "done"]},
            "count": {"type": "integer", "minimum": 1}, "label": {"type": "string"},
            "wait_kind": {"enum": ["ci", "deploy", "clock", "card", "agent"]}
          }
        }}
      }
    },
    "sections": {
      "type": "object",
      "required": ["needs_you", "stuck", "running", "waiting", "ready", "done"],
      "properties": {
        "needs_you": {"type": "array", "items": {"$ref": "#/$defs/ask"}},
        "stuck": {"$ref": "#/$defs/rows"}, "running": {"$ref": "#/$defs/rows"},
        "waiting": {"$ref": "#/$defs/rows"}, "ready": {"$ref": "#/$defs/rows"},
        "done": {"$ref": "#/$defs/rows"}
      }
    }
  },
  "$defs": {
    "rows": {"type": "array", "items": {"$ref": "#/$defs/row"}},
    "row": {
      "type": "object",
      "required": ["id", "key", "kind", "gateway", "profile", "board", "title", "detail", "details", "actions"],
      "properties": {
        "id": {"type": "string"}, "key": {"type": "string"},
        "kind": {"enum": ["card", "question", "subagent", "process", "machine", "decision", "blocker"]},
        "parent": {"type": ["string", "null"]},
        "depth": {"type": "integer", "minimum": 0, "maximum": 4},
        "nested_detail": {"type": "string"},
        "wait_kind": {"enum": ["ci", "deploy", "clock", "card", "agent"]},
        "status": {"type": "string"},
        "section": {"enum": ["needs_you", "stuck", "running", "waiting", "ready", "done"]},
        "gateway": {"type": "string"}, "profile": {"type": "string"}, "board": {"type": "string"},
        "title": {"type": "string"}, "detail": {"type": "string"},
        "details": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "object", "required": ["kind", "label"]}}
      }
    },
    "ask": {
      "allOf": [{"$ref": "#/$defs/row"}],
      "required": ["source", "situation", "options", "only_you", "request_id", "request_revision", "recommend_letter", "reason"],
      "properties": {
        "source": {"enum": ["card", "question"]}, "situation": {"type": "string"},
        "only_you": {"type": "string"}, "recommend_letter": {"type": "string"}, "reason": {"type": "string"},
        "request_id": {"type": ["string", "null"]}, "request_revision": {"type": ["integer", "null"]},
        "options": {"type": "array", "items": {
          "type": "object", "required": ["letter", "name", "detail", "recommended", "reason", "choice_id"],
          "properties": {
            "letter": {"type": "string"}, "name": {"type": "string"}, "detail": {"type": "string"},
            "recommended": {"type": "boolean"}, "reason": {"type": "string"},
            "choice_id": {"type": ["string", "null"]}
          }
        }}
      }
    }
  }
}
```

`revision` is SHA-256 of the canonical JSON model excluding revision itself, not
the feed's monotonic `rev`. Identical snapshots have identical revisions.
`header.status` carries the validated core `outcome_json.status` envelope unchanged;
`header.state` copies that envelope's `kind`. Desktop paints only its published
`presentation.stripe`, never counts or a local classifier. Missing, unsupported,
malformed or unavailable protocol means `status=null`, `state=unknown` and an
attention palette. Green requires `kind=done` and complete coverage. Identity
coordinates are nonempty strings and revision/generation are nonnegative JS-safe
integers; boolean or string versions are unsupported. Legacy completion prose or
`outcome_json.kind=done` alone is not canonical completion evidence.
Counts are unique displayed rows per section; pips are nonempty counts in section
order. Diagnostics distinguish unavailable extra inputs from a known empty source.

An `open_card` action has `target:{id,gateway,profile,board}`. A `decide` action
has `request_id`, `revision`, `owner:{gatewayId,profile,storedSessionId}`. The
renderer must route to that exact owner and use the existing user-steps decision
POST, including its own idempotency key and revision check. New producers also
publish `source_owner` (a boolean established from the serving gateway's trusted
install-id aliases) and `admission:{state,resolved,scope_complete,choices}`.
`choices` contains canonical `{id,effect,label}` only; `scope_complete` requires
literal true and nonblank canonical scope. Full consent text remains in Details.
The renderer routes source-owned asks to the model's connection/profile without
network discovery on click. Foreign installation owners retain verified GET
routing: the Desktop hub-name map is not an installation-id registry and must
not be used to infer an owner's connection. Missing admission from an older
producer retains the verified GET path; malformed admission never falls back.
This is a snapshot, not
authority: the owner POST revalidates current revision, scope, choices and state.
The renderer must not execute
card scope, choice text, or arbitrary commands. Card asks not yet mirrored carry
null request/choice IDs and no decide action: P3 widens the mirror. They retain
Open card, never a fabricated answer binding.

## Inputs and ownership

- `dashboard/feed.py`: existing lineage, linked tasks, done history, card detail
  chain; `computed_at` is the read snapshot clock.
- `inputs.py`: query-only current action-store revisions with current lifecycle
  state/resolution (not the frozen revision's old state), canonical session
  outcome, core subagent/process annotations, existing rentals service snapshot
  filtered by exact gateway/profile/session ownership. No mirror reconciliation,
  migrations, decisions or cloud lifecycle writes.
- `model.py`: `reduce_panel(bundle, now=...)`; pure, no reads or implicit clock.
  Core's execution-observation selector owns freshness/identity semantics. With
  no core projector, evidence is unknown, not guessed running.
- `brief.py`: the shared raw grammar (`sections`, `options`) used by ask-gate;
  `parse_brief(human_request)` adds final display fields. Choice labels control
  order and own letters (b262ccfb semantics), detail joins by letter, never index.
  When own letters are missing/duplicate or leave no room for Skip/Other, badges
  are positional. The recommendation paragraph excludes appended evidence/footer.

## Classification port

Order matters, matching Desktop `classify`:

1. Processes/subagents: running only with current canonical execution evidence;
   otherwise Waiting. Rentals count as In progress rows. No elapsed-silence guess.
2. Ignore terminal chips. Unconfirmed remote completion remains Waiting as
   reported, never Done; stale last-known open work becomes status unknown.
3. Current human wait -> Needs you. Exact canonical aliases deduplicate mirrored
   requests. Retired/resolved requests do not re-open decisions; a still-blocked
   owner remains discoverable as status. **Never infer a human wait from preserved
   `block_kind` alone**. Scheduled external watches without requests stay Waiting.
4. Nonhuman triage -> red Stuck once the current episode exceeds 600 seconds,
   with `attention=triage_overdue`, never Needs you. Before that threshold (or
   with unknown time), nonhuman triage/administratively held blocked cards -> Waiting,
   blue (`wait_kind=agent`), naming the chat agent and its current decision reason.
   Retained next steps, spend holds and
   creator decisions are presentation evidence, never permission to resume. Canonical nonhuman
   wait ownership suppresses preserved old human fields; a currently open owner-bound request
   remains an ask. Named CI/deploy/clock and running-card producers take precedence and paint
   light blue. Shared off-list held blockers use the same palette and actor wording.
   Every triage row and triage dependency label includes time since its latest entry
   into triage, derived from lifecycle event transitions, not `created_at`. Repeated
   hold notices within one triage episode do not reset the age. Missing history says
   `entry time unavailable`. Wire elapsed templates tick locally without revision
   churn. Scope stops, run caps and retained decisions name their actual cause,
   rather than pretending they are awaiting specification/promotion.
   Blocked upstream -> Waiting, unless core says stalled. Core stalled verdict
   precedes unknown execution evidence and fresh parked lifecycle.
5. Fresh (<=60s) parked board lifecycle may explain ready/todo/review/triage/
   scheduled/blocked without proving executor liveness. Expired/missing/ambiguous
   run evidence -> explicit unknown Waiting, not running.
6. Running card with known progress >=2h old -> Stuck; creation/first claim does
   not establish progress. Otherwise In progress. Observed review -> In progress.
7. Ready -> Ready; todo with no dependency -> not started. Other waits -> Waiting.
8. Done history: owner-qualified dedup, newest first, only confirmed done/archived
   rows; stale/unfinished reports are excluded. A local done row carries
   `run_stats` (`runs`, `run_seconds`, `cost_usd`, `priced_runs`, `usage_runs`,
   `tokens`; `feed.done_run_stats` from `task_runs` + `worker_result`/`run_route`
   events). Its detail reads `42m · 2 restarts · $3.10`: summed run time,
   restarts = runs - 1, dollars = worker-reported `cost_usd`, else the run's
   tokens at the routed model's API list price (subscription routes priced as
   their API provider). `~$` marks runs with usage it could not price
   (class-routed runs name no model); no priced run shows the token count, never
   a guessed dollar figure. Rows without `run_stats` (origin history, old
   producers) keep the result text. Any row with `completed_at` leads with its
   completion age (`3 h ago · 42m · 2 restarts · $3.10`) as a wire `ago` clock
   part, so the age ticks locally without changing the revision; details start
   with `completed <Pacific time>`.

### Deliberate shape/presentation differences

- The agreed six sections have no `queued`, `reported`, or `blockedUpstream`:
  queued maps to Ready with `queued:true`; other two map to Waiting. No row lost.
- Desktop-only switch-cache grace is absent: the hub has an explicit read clock,
  never `cachedAt`/`paintedAt`. Running uses canonical source evidence as before.
- Stuck timestamps use Pacific explicitly rather than the Desktop machine's
  local timezone. Ready uses the board's published queue/capacity reason rather
  than guessing worker capacity for lane admission.
- This P1 model does not yet reproduce the renderer's expanded interactive chain
  disclosure, cached title reads, nudge drafting or fold state. Existing raw
  fields remain available during cutover; actions currently encode Open/Answer.

### Status-line rules (audit t_01a2001662ee259d)

- Worker clocks may lead the read clock by up to `SKEW_S` (120 s): `now` is lifted to the newest observation
  before core's projector runs; a larger lead stays core's `clock_inconsistent`.
- A remote row whose replica placeholder has no run id (`missing_qualified_identity`; core reports
  `invalid_requested_identity`) is a producer gap, not an unknown state: a fresh (`0–60 s` read,
  numeric `synced_at` with `0 <= now - synced_at <= 300 s`; no future skew allowance)
  running row is shown In progress with `evidence_state: "reported"` and a Details line saying so; parked
  states show their board state. No `· board status` tail on any row.
- Named dependency waits use `waiting on: <hydrated title> · age` with an `open_card` action labeled `Open dependency` (`role:'dependency'`).
- A card row also carries `open_session` (`target:{session,gateway,profile}`) when its `session_id` names a chat
  outside this panel's lineage. Desktop shows Open session / Open card / Open dependency only in the row's
  right-click menu, never as inline links (an ask row keeps its own Open card button; CI/PR links stay buttons).
  Resolve the primary explicit producer first; within that dependency, follow hydrated unfinished parents to the
  deepest blocker (bounded, cycle-safe, deterministic ties). For a pure parent wait, consider all hydrated parents.
  Never link an own-card wake check to itself, reinterpret clock/CI/deploy waits as parents, or substitute a secondary
  mention when the primary is unavailable. Show the leaf title/status, `on <gateway> (<hostname>)` when known, and
  its latest current awaiting/block/creator-ask reason (`asks origin: …` for chat waits); running work never reuses
  a historical ask. Replica actions require a known owner. Missing hydration uses a short category. Full reasons and
  parent/release coordinates stay in `details[]`. No additional reader I/O is introduced.
- Waiting text keeps a short category or specific blocker reason; URLs and hashes stay in Details, not inline.
  Own waits include their gateway, e.g. `waiting on CI · PR #3881 · 2m on remote`; URLs also go to an `open_url` action.
  A CI wait's first URL action carries `inline: "CI"`: Desktop draws that word of the detail as the link instead of
  an `Open CI` button. Waiting/Ready rows are one line (title left, detail right); `details[]` is the row tooltip,
  not a Details disclosure.
- Answered-but-undelivered asks are one `decision: {request_id, text}` line on the card they unblock (own row
  if card-less) and vanish when the request resolves; header `label` never reads "unresolved" for them.
- Running rows without `run_model` use the current run's `run_route`, else `model_tier`, event model.
- Running, review and stuck rows append `N failure(s)` to `detail` when the hydrated card detail's `task.failed_runs` (count of the card's runs ending crashed/spawn_failed/oom_killed/timed_out/gave_up/failed/signaled; reclaims and rate limits excluded) is a positive integer. Missing, null (unreadable run table), zero or non-integer adds nothing.
- Titles lose emoji-presentation glyphs; Done rows carry no redundant `done` detail.

## Optional actionability v1 diagnostics

`CoreReaders._detail` optionally carries the frozen core
`hermes_cli.kanban_actionability_sources.describe(connection, task_id, now=0)`
projection. Zero is intentionally **time-neutral**, not an observation clock:
absolute source deadlines, contract keys and first-observed evidence are retained;
clock-derived scheduled/overdue action changes are not stored. Existing snapshot
and observation clocks remain the only freshness authority. The call uses the
existing query-only detail connection, one task and its immediate parents, never
a report, lifecycle writer, migration or network refresh. Older cores and malformed
optional evidence leave hydration intact.

Backend `actionability.present` validates the diagnostic/detail/task identity and
owner coordinates before adding `row.actionability` and `Actionability …` strings
to `details[]`. It does not change classification, section/header counts, actions,
ask/decision bindings, hierarchy, waits or execution evidence. Local rows require
live local detail; replicas require matching board and recorded owner and always
retain UNKNOWN evidence. Unhydrated, mismatched and unsupported inputs explicitly
say unavailable. Ordinary terminal rows remain omitted; terminal detail/history
consumers show n/a, never missing-owner violations. Deadline absence remains null
and “deadline unavailable”; no timestamp is invented. Reducer `now` may add an
absolute escalation-overdue label, never scheduled-wake-overdue.

The renderer is unchanged: normal rows use existing tooltips; asks keep their
Details disclosure. Old wire payloads without actionability remain supported.
Within existing source capture, a stateless demand-only stale check covers wake-check
commands, pending retain-command custody and sync mode (inputs outside the existing
core revision cursor). Each local detail records `actionability_inputs` in the same
query-only SQLite read snapshot as its diagnostic; a pre-existing caller snapshot
is preserved. Capture compares current inputs with that hydration baseline, never
with a previous capture's demand set. Bootstrap, new demand and rehydration cannot
by themselves re-admit unrelated producers, while a first real write before the next
capture is retained. The signature is acquisition metadata, not diagnostic authority
or an observation clock. Immediate-parent and source-named administrative-owner
references participate in existing semantic invalidation, including unhydrated
parents. No new poller or source admission policy is introduced. The unactivated
`board_capture.py` acquisition capability intentionally omits both the diagnostic
and its signature on fresh and reused details: it cannot certify complete
incremental invalidation and remains unactivated.

Tests: `tests/test_actionability.py`, the persisted shared fixture helper, and
`desktop-plugins/session-panel/test/actionability.test.mjs` exercise real
CoreReaders → reducer → actual DOM. The bounded fixture threshold is 65 SQL
statements for one demanded detail (measured baseline 10 plus 34 projection
statements for a child with one immediate parent; allowance for small schema
variance). This is a statement-count/demand bound, **not** an absolute history-volume
bound: core's per-task, kind-filtered events reader has no LIMIT. Read-only proof
compares logical database state and main DB/WAL bytes; SQLite SHM reader slots may
change.

## Tests and private replay

Run `scripts/test-all --no-core --exact -k plugins/session-panel -k plugins/ask-gate
-k plugins/user-steps` (also run `repo-guards` explicitly if this runner does not
select it). Tests cover own-letter C/A ordering and answer bindings, remote and
triage asks, stale human block kind with an external watch, blocked/no-request,
stalled vs unknown, old progress, subagents/processes/rentals, retired asks,
terminal/done dedup, canonical header, lifecycle reads and determinism.

Real hub dumps contain personal/employer/network details and are **private task
attachments**, not public fixtures. Replay them against this reducer with source
clocks pinned to their recorded snapshot; keep provenance and historical versus
current state explicit. Do not normalize labels, IDs or source timestamps.
