# PanelModel v1

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
and doing three sequential GETs. It then requests `POST /decision-refresh {session}`
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
    "header": {
      "type": "object", "required": ["state", "status", "source", "counts", "pips"],
      "properties": {
        "source": {"enum": ["canonical", "derived"]},
        "state": {"type": "string"},
        "status": {"type": ["object", "null"], "description": "Validated core status_version=1 envelope, preserved with identity, revision, generation, coverage and presentation."},
        "counts": {
          "type": "object",
          "required": ["needs_you", "stuck", "running", "waiting", "ready", "done"],
          "additionalProperties": {"type": "integer", "minimum": 0}
        },
        "pips": {"type": "array", "items": {
          "type": "object", "required": ["section", "count", "label"],
          "properties": {
            "section": {"enum": ["needs_you", "stuck", "running", "waiting", "ready", "done"]},
            "count": {"type": "integer", "minimum": 1}, "label": {"type": "string"}
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
        "kind": {"enum": ["card", "question", "subagent", "process", "machine"]},
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
POST, including its own idempotency key and revision check. It must not execute
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
4. Blocked upstream -> Waiting, unless core says stalled. Core stalled verdict
   precedes unknown execution evidence and fresh parked lifecycle.
5. Fresh (<=60s) parked board lifecycle may explain ready/todo/review/triage/
   scheduled/blocked without proving executor liveness. Expired/missing/ambiguous
   run evidence -> explicit unknown Waiting, not running.
6. Running card with known progress >=2h old -> Stuck; creation/first claim does
   not establish progress. Otherwise In progress. Observed review -> In progress.
7. Ready -> Ready; todo with no dependency -> not started. Other waits -> Waiting.
8. Done history: owner-qualified dedup, newest first, only confirmed done/archived
   rows; stale/unfinished reports are excluded.

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
- Named dependency waits use `waiting on: <hydrated title> · age` with an `open_card` action labeled `Open parent card`.
  Resolve the primary explicit producer first; within that dependency, follow hydrated unfinished parents to the
  deepest blocker (bounded, cycle-safe, deterministic ties). For a pure parent wait, consider all hydrated parents.
  Never link an own-card wake check to itself, reinterpret clock/CI/deploy waits as parents, or substitute a secondary
  mention when the primary is unavailable. Show the leaf title/status, `on <gateway> (<hostname>)` when known, and
  its latest current awaiting/block/creator-ask reason (`asks origin: …` for chat waits); running work never reuses
  a historical ask. Replica actions require a known owner. Missing hydration uses a short category. Full reasons and
  parent/release coordinates stay in `details[]`. No additional reader I/O is introduced.
- Waiting text keeps a short category or specific blocker reason; URLs and hashes stay in Details, not inline.
  Own waits include their gateway, e.g. `waiting on CI · PR #3881 · 2m on remote`; URLs also go to an `open_url` action.
- Answered-but-undelivered asks are one `decision: {request_id, text}` line on the card they unblock (own row
  if card-less) and vanish when the request resolves; header `label` never reads "unresolved" for them.
- Running rows without `run_model` use the current run's `run_route`, else `model_tier`, event model.
- Titles lose emoji-presentation glyphs; Done rows carry no redundant `done` detail.

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
