# Completion receipts (`completion_receipt` v1)

Patches **2244–2253**.

## Behavior

A workflow `done` says the work item finished; it does not say what happened to the artifact. A **completion receipt** is an additive, bounded record, attached to a `done` declaration or a Kanban completion, that states per scope whether the artifact was `delivered`, `superseded`, `contained` or `rejected`, with evidence and any remaining work. It is attribution, never authorization: a receipt does not waive acceptance gates, grant permission to run deferred work, or prove its own claims.

- **Absence means unspecified.** A legacy `done` or completion has no receipt and implies nothing about artifact acceptance. A later plain `done` never inherits an earlier receipt.
- **Whole-value validation, no stripping.** A malformed receipt is refused with a `completion_receipt: …` error before any write; unknown fields are errors, not ignored.
- **Pending work needs an admitted owner.** `pending` remaining work must name a fully qualified owner that existing ownership links can corroborate; `deferred` work may have `owner: null` and grants no authority. Unknown, stale, closed, self-referential or merely plausible owners are refused (fail closed).
- **Current observation and historical delivery stay separate.** The last receipt is kept as history with its original source and clock while the session's *current* kind moves on (`running`, `waiting`, `decision`, `muted`, `unresolved`, a new unrelated `done`). Refreshing never re-admits or re-verifies it. Remote rows carry the receipt unchanged while still reporting `stale`/`unfinished` from their own gates; a stale or unverified owner row keeps the receipt as history but never counts as current delivery.
- **Surfaces.** Desktop session-outcome tooltip, sidebar list rows, and `hermes sessions outcome --json` show the receipt and source verbatim; the CLI/tool completion paths refuse malformed receipts without writing.

### Receipt shape

```json
{"version": 1, "items": [{
  "scope": "bounded assessment artifact",
  "disposition": "delivered | superseded | contained | rejected",
  "evidence": [{"ref": "https://example.com/review/1",
                "observed_at": "2026-01-01T00:00:00Z",
                "verification": "reported | independently_checked"}],
  "remaining": [{"scope": "…", "state": "pending | deferred",
                 "owner": "@task:board@gateway/id | @session:profile@gateway/id | null",
                 "reason": "…"}],
  "superseded_by": "…"}]}
```

`superseded_by` is required for `superseded` and forbidden otherwise. Limits: 1–32 items, 1–32 evidence entries and 0–32 remaining entries per item, text ≤ 2000 characters (refs and owners ≤ 2048, `observed_at` ≤ 64), no control characters except newline/tab, `observed_at` ISO‑8601 **with** timezone, version integer `1` (not `true`), canonical JSON ≤ 32768 bytes. `independently_checked` is attributed evidence only. `completion_receipt` is valid only on a `done` declaration.

## How it works

1. **Structural validation (2244).** `hermes_completion_receipt.validate_completion_receipt` returns an independent copy or raises `ValueError("completion_receipt: …")`. `hermes_state_outcome.validate_declaration` calls it for `done` and rejects the key on other kinds. A rejected redeclaration leaves the stored outcome unchanged.
2. **Admission at the canonical writer (2245).** `kanban_completion_receipt.prepare` runs at the start of `complete_task`, before side-effecting gates; `recheck` runs inside the write transaction before any event or terminal write. Owner rules:
   - Same board/gateway `@task:` owner: must be an open (`todo ready running scheduled review blocked triage`) card, not the completing card, either a linked child or a card created by it, and locally authoritative (a synced card created on another gateway cannot be named as local). Re-checked under the lock.
   - Remote `@task:` owner: requires an existing qualified wait link (`awaiting` kind `card` matching gateway/board/ref) **and** a fresh replica read (`kanban_wait_replica.read_task`) showing an open status. The network read happens once before the transaction; only the link is re-checked inside it.
   - `@session:` owners are refused for kanban completion (a requester/shepherd link is not acceptance of the pending obligation).
   - Session `done` declarations (`check_session_declaration`) resolve pending `@task:` owners against `session_tasks(lineage)`: exactly one fresh, open linked card, with local authority or a fresh remote read. `@session:` owners are refused; there is no session-to-session wait store.
3. **Historical provenance (2245, 2250).** Session declarations store `completion_receipt_source` (`kind: session_declaration`, session id, profile, gateway, `declared_at`). `historical_fields` re-reads the last valid receipt from the stored outcome and `refresh` merges it under the freshly derived current state. A declared `done` carries its own receipt or none; a derived `done` keeps the last receipt only if no active user message is newer than `declared_at`; a new non-`done` declaration keeps history, a new plain `done` does not.
4. **Task-run projection (2245, 2246).** `read_completed` reads the latest `completed` event joined to its own task's run (cross-task run ids never project), requires run outcome `completed`, revalidates the receipt, and returns `completion_receipt_source` `{kind: task_run, ref: @task:board@gateway/id, event_id, event_uid, run_id, run_uid, completed_at}`. Callers must pass the authoritative source gateway and board. Source UIDs survive replica integer remapping; integer ids are store-local. `read_inventory` handles older schemas (no receipt rather than invented acceptance). 2246 adds an admission marker (`completion_receipt_admission == 1`, integer) in the completed event payload, set by the loaded `complete_task` (`complete_task.completion_receipt_admission = 1`); metadata without it, from older writers or `request_review`/other run outcomes, is not a receipt. Review runs and later non-completed runs never replace an earlier completed receipt.
5. **Evidence preservation (2246).** Receipt `ref`/`superseded_by` strings are never rewritten (`_rewrite` skips `completion_receipt`). If a ref points into a managed scratch workspace, completion must have preserved that file; the existing `attached_from_workspace` map is carried in the source as relocation provenance, not as a new observation. Caller-supplied attachment maps are discarded when a receipt is present, ambiguous aliases (`file://…`, embedded paths, `./x`) and unpreservable files raise `ArtifactPreservationError` and roll back, and staged copies are deleted on any failure including commit failure (`discard_on_error`).
6. **Entry-path refusals (2249–2251).** `kanban complete` validates the receipt shape before the goal judge or any write; tool `kanban_complete` does the same via `require_completion_metadata`; `--metadata` parsing (`kanban_cli_metadata.parse_metadata_flag`, exit 2) validates it at parse time; `receipt_refusal` turns admission and preservation errors into `cannot complete <id>: …` and re-raises unrelated errors.
7. **Edit cannot forge (2252).** `kanban edit` metadata goes through `keep_admitted_receipt`: omitting `completion_receipt` keeps the admitted one, resubmitting the identical value is allowed, any different value raises `ReceiptEditRefused` (a `ValueError`; the CLI reports it like other refusals) because it would ride the original admission marker unchecked.
8. **Remote projections (2253).** `validate_receipt_source` bounds task-run provenance: exact field set (`kind ref event_id run_id completed_at` plus optional `event_uid run_uid attached_from_workspace`), `kind == task_run`, `ref` equal to the qualified record, positive int64 store-local ids (bool rejected), nullable UIDs ≤ 256 characters, finite non‑negative `completed_at`, ≤ 32 relocations that refer only to receipt refs, text ≤ 2048, total ≤ 16384 bytes. It also guards local DB reads. `remote_fields` transports receipt+source from an owner publication only when gateway, profile, board and id match the binding and the row is attested (`_owner_evidence`); `kanban_origin_ledger.project` and `kanban_session_activity._read_remote_links` splice it in without changing `stale`, `unfinished`, `status` or `released`.
9. **Desktop (2247, 2250).** `outcomeTooltip` appends a historical `completion_receipt` block after the workflow text without changing `outcomeStripe`. The renderer revalidates the same bounds and drops the *whole* receipt on any malformed item; it JSON‑escapes values and Unicode line/bidi separators so scope text cannot forge lines, shows only source identity fields (store-local ids labeled, relocation paths never shown, unqualified sources labeled `unqualified`), uses the source clock rather than the later `at`, and labels a deferred non-null owner `owner (unverified)`.

## Code

- `hermes_completion_receipt.py`: validator, `historical_fields`.
- `hermes_state_outcome.py`: declaration validation, session admission and source stamp, historical merge (`refresh`/`get_outcome`), later-`done` rule.
- `hermes_cli/kanban_completion_receipt.py`: `prepare`, `recheck`, `check_session_declaration`, `keep_admitted_receipt`, `ReceiptEditRefused`.
- `hermes_cli/kanban_completion_receipt_projection.py`: `read_completed`, `read_inventory`, `validate_receipt_source`, `remote_fields`.
- `hermes_cli/kanban_db.py`, `kanban_db_edit.py`, `kanban_metadata_files.py`: writer hook, admission marker, evidence preservation, edit guard.
- `hermes_cli/kanban.py`, `kanban_cli_metadata.py`; `tools/kanban_tools.py`, `kanban_tools_completion.py`: entry-path refusals.
- `hermes_cli/kanban_origin_ledger.py`, `kanban_session_activity.py`: remote passthrough.
- `apps/desktop/src/lib/session-outcome.ts`, `src/types/session-outcome.ts`: tooltip and types.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**; the parent's full-tree replay proof is separate.

- `tests/hermes_state/test_completion_receipt.py`: all dispositions, malformed rejection without mutation, size bound, persistence across reopen/refresh, history after new turn/running/muted/decision, session pending-owner admission, later plain `done` (declared and derived) not inheriting, list rows and `outcome --json` carrying receipt+source (2248).
- `tests/hermes_cli/test_kanban_completion_receipt.py`, `…_owners.py`: stored on the completed run, owner qualification (missing, unrelated, closed, wrong gateway/board, self, foreign pickup, forwarded work, requester session), in-transaction races, stale replica.
- `tests/hermes_cli/test_kanban_completion_receipt_projection.py`: source provenance, review/legacy/cross-task cases, unadmitted markers.
- `tests/hermes_cli/test_kanban_receipt_artifacts.py`: identity kept through relocation, rollback and cleanup, forged relocation, aliases.
- `tests/tools/test_kanban_complete_receipt_entry_paths.py`: direct handler, `tool_call` bridge (default bridge does not invoke completion; forwarding variant does), in-process CLI and a real `hermes kanban complete` subprocess (the `execute_code` route goes through terminal), refusal before the goal judge.
- `tests/hermes_cli/test_kanban_completion_receipt_edit.py`: edit cannot swap but keeps the receipt.
- `tests/hermes_cli/test_kanban_receipt_source_bounds.py`, `test_kanban_remote_completion_receipt.py`: provenance bounds and both remote producers (ledger, activity), including stale, unattested, tampered and collision cases.
- `apps/desktop/src/lib/session-outcome.test.ts`: tooltip rendering, escaping, bounds, unqualified sources, stripe unchanged.

## Applying

Use only the complete ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply a subset: later patches depend on earlier helpers and the admission marker. No configuration is required.

## Gotchas

- A receipt is shape-checked, not truth-checked; `independently_checked` is a claim and grants nothing.
- Receipts written before 2246 lack the admission marker and are not projected as receipts.
- Remote owner admission depends on a fresh replica read; an unavailable or stale replica refuses the completion rather than guessing.
- Callers of `read_completed` must pass the true source gateway/board; the observing host is wrong for replicas.
- Receipt evidence in a scratch workspace must be an exact absolute path or the completion is refused.
- Deferred owners are not verified; the UI marks them so.
- Sessions in other gateways can be named only through task owners with an existing link.

## plugin?

No. Admission, run metadata, session outcomes and the Desktop tooltip are core contracts that need the canonical writers and cannot be hooked from a plugin.
