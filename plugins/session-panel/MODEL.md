# PanelModel v1

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
      "type": "object", "required": ["state", "counts", "pips"],
      "properties": {
        "state": {"type": "string"},
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
`header.state` copies the session's canonical `outcome_json.state` or `.kind`;
missing/unreadable outcome means `unknown`, never a state inferred from counts.
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
  Resolve only the primary dependency from existing bundle/card evidence; never link an own-card wake check to itself,
  reinterpret clock/CI/deploy waits as parents, or substitute a secondary mention when the primary is unavailable.
  Replica actions require a known owner. Missing hydration uses a short category; service/PID/task identifiers and
  parent/release coordinates stay in `details[]`. No additional reader I/O is introduced.
- Waiting text is one short phrase (`waiting on CI · PR #3881 · 2m`, `waiting on hub deploy · 15m`); URLs go to an
  `open_url` action, the producer's wording to a `Waiting on:` Details line. Hashes never appear inline.
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
