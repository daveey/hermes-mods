# Kanban shared blockers: provider scope, clearing evidence and live owners

Original feature: 0141. Follow-ups covered here: **1511–1514, 1523, 1534, 1557, 1567–1568**.
Later behavior below supersedes the original rule that a park without the id cleared a wall.

## Behavior

- `kanban_block`, `kanban_continue` and `kanban_schedule` accept `blocker_id` plus optional
  `blocker_provider`, `blocker_model` and `blocker_lane` strings. Provider scopes the wall;
  model/lane describe the observation, not extra restrictions or the task's future routing.
  Invalid scope types are refused before transition. A clear and an assertion cannot be combined.
- A scoped wall does not hold a card's known other-provider step. Unknown next routing remains
  conservatively held. Worker context shows current route and each wall's applicability: finish
  unrelated work, then wait at the step that needs the affected provider.
- Explicit scope wins. Otherwise inference uses only provider-labelled wall lines in the current
  reason/checkpoint, and only an offline-recognized model provider. Unlabelled, ambiguous or
  service-provider errors remain board-wide. The executor's stored error, pins and profile are
  **not** evidence of which provider the later step encountered (1512 supersedes 1511's inference).
- Scope is the union of all assertions of an id in the 14-day lookback. Any unscoped assertion or
  differing provider makes it board-wide. Reassert a scoped id with the same `blocker_provider`;
  use a new id for a narrower wall.
- A wall is open only when its newest assertion is **strictly newer** than its newest clearing
  **and at least one live card remains behind it**. Done/archived cards cannot keep it open.
  Losing its last owner is not itself provider-clear evidence; a later encounter can assert it again.
- `completed`, `review_requested` and `changes_requested` runs never assert a blocker, even when
  stale metadata still carries `blocker_id`. They cannot replace its assertion timestamp, reason
  or provider. For a scoped wall, implicit completion/handoff evidence must come from a known
  matching historical route. Other-provider and unknown historical routes neither probe nor
  implicitly clear it. Explicit `clear_blocker` attestation can clear it on any route.
- A re-park without the id says nothing about the wall. Clearing also retains the existing
  condition-wake evidence rule: the successful check must belong to a park that carried this id.
- One applicable probe runs at a time, with `kanban.shared_blocker_probe_seconds` spacing
  (default 1800). A timed blocker park retains its wake/check rather than becoming an untimed wait.
- After a clear, parked cards are released; already queued `ready`/`review` cards get one
  `shared_blocker_cleared` marker per clear without changing lane or bypassing capacity/admission.
  Diagnostics call an old `respawn_guarded` event the **last observed** hold, not a verified current
  hold. Same-second ordering uses event id after timestamp.

## How it works

`blocker_metadata` centralizes validation and metadata propagation across block, continuation,
explicit schedule and timed park. `blocker_states` derives assertions, clearing and live ownership
from run/event history; the assertion scan remains bounded to 14 days. In 1534 the initial scan uses
`run_effect`, just like the per-card walk, and `BlockerState.is_open` also requires `behind`.

`apply_model_tier` records a `run_route` admission receipt even with tiering off. Historical reads
prefer that receipt, then legacy `model_tier` events, never today's pin/config. A receipt saying
unknown is authoritative. Next-route preview is read-only and retains unknown class allocation.

1513 replaces two empty-dict fixture lambdas with `dict`. 1514 adds traceback-bearing fallback logs
and moves attachment basename/collision helpers and the tool park guard out of large facades;
these are refactors, not new attachment or wake semantics.

## Code

- `hermes_cli/kanban_shared_blockers.py`: state derivation, route evidence, guard, release and context.
- `hermes_cli/kanban_blocker_metadata.py`, `kanban_model_tier.py`: scope and admission receipts.
- `hermes_cli/kanban_db.py`, `kanban_continuation.py`, `kanban_db_schedule.py`, `kanban_timed_park.py`.
- `tools/kanban_tools.py`, `kanban_tools_park_args.py`, `kanban_tools_schemas.py`.
- `hermes_cli/kanban_diagnostics.py`: historical hold wording and timestamp/id ordering.
- 1514 also touches `hermes_cli/kanban_db_attachment_names.py` and
  `plugins/kanban/dashboard/plugin_api.py` for the extracted attachment helpers.

## Tests

Source regression coverage read from these patches; **not executed in this documentation pass**:

- `tests/tools/test_kanban_blocker_metadata.py`: `test_scope_round_trip` and
  `test_invalid_scope_refused_without_transition` parameterize all three tools;
  `test_unlabelled_wall_never_borrows_current_receipt`,
  `test_stored_run_error_never_borrows_current_receipt`,
  `test_unknown_provider_label_is_not_inferred`, `test_known_model_provider_label_still_inferred`,
  `test_explicit_scope_is_never_filtered_by_registry`, direct continuation and untyped classification cases.
- `tests/hermes_cli/test_kanban_shared_blockers.py`: scoped next/review lanes, other-provider
  non-probes, matching clears, conservative unknown routes, read-only retry preview, historical pins,
  admitted route receipts with tiering off, scope union, explicit clears and reassertion guidance.
  1534 adds `test_finished_other_route_wall_has_no_live_owner` (continue/schedule × done/archived),
  `test_terminal_outcome_blocker_metadata_is_not_assertion` (all three clear outcomes), and
  `test_live_owner_wall_stays_open_and_renders` (blocked/scheduled/ready/running).
- `tests/hermes_cli/test_kanban_cleared_ready_hold.py`:
  `test_clear_retires_ready_hold_before_capacity_and_dispatches_next_tick` covers ready/review through
  actual dispatcher decisions with stubbed spawns; the other cases verify historical wording and
  same-second ordering in either input list order.
- 1513 is fixture lint only. 1514 adds no tests; its logging and extraction changes have not been
  independently exercised here. No live provider outage, Desktop rendering or REST scope parity is claimed.

## Applying

Apply the complete ordered mirror using the [README replay recipe](README.md#applying), including
its replay plan where specified. These shared dispatcher/tool files are not independent patches.
Keep 1511 and 1512 together: the latter tightens inference and adds durable admission receipts.

## Gotchas

- Workers must provide a blocker id; free-form reason similarity never creates one.
- Unknown next route means hold, whereas unknown historical route means no implicit clear evidence.
- Ownerless expiry does not forge a clearing run; do not loosen provider matching to simulate expiry.
- Clearing queued evidence does not mean the card has capacity or pickup on this machine.
- Successful patch replay is not a runtime test or proof of deployment.

## Plugin?

No separately installable plugin: these are core dispatcher transactions, routing receipts and worker
context. The dashboard import change in 1514 follows a core helper extraction.


## Historical patch 0141 reference

The original documentation follows for historical code/test provenance. Its open-wall and
implicit-clearing rules are superseded by the current Behavior above; the old seven-test
result is historical, not a rerun in this pass.

### Original shared-blocker introduction (0141)

Patch:

- `patches/0141-feat-kanban-park-cards-behind-one-shared-external-bl.patch`: a new
  `hermes_cli/kanban_shared_blockers.py`, small hooks in `kanban_db.py`, `kanban_db_dispatch.py`,
  `kanban_continuation.py`, `kanban_ops.py`, `config_defaults.py`, `agent/prompt_builder.py`,
  `tools/kanban_tools.py` and `tools/kanban_tools_schemas.py`, plus
  `tests/hermes_cli/test_kanban_shared_blockers.py`.

What you see: when many cards hit the same wall outside any one card (GitHub Actions billing refused, a
provider outage, a release pipeline down), the board stops spending a worker run per card to rediscover it.
One card checks whether the wall is still there every 30 minutes, and the others wait. When the wall goes
away, the waiting cards go back to work by themselves, with no human unblocking each one. The motivating case
was 93 runs on 20 cards, each finding the same Actions billing refusal.

### Behavior

- `kanban_block` and `kanban_continue` take an optional `blocker_id`, a short slug such as
  `github-actions-billing`. Workers are told (tool schema + `KANBAN_GUIDANCE`) to set it only when the wall
  would stop other cards too, and to reuse an id listed under "Open shared blockers" in their context.
  The id is normalized to lowercase `[a-z0-9._-]`, at most 64 characters. An unusable value is refused with
  "blocker_id must be a short slug ...". The tool result echoes `blocker_id`.
- **Open blocker.** A card is behind blocker X while its latest closed run that says anything about the
  outside world carried X. X is open while its newest assertion is newer than its newest clearing.
- **One probe.** While X is open, the dispatcher's respawn guard holds every card behind X (guard reason
  `shared_blocker`, listed under `respawn_guarded` / "Guarded (shared_blocker)") unless:
  - no other card behind X is `running` (one probe at a time, always), and
  - `kanban.shared_blocker_probe_seconds` (default 1800) have passed since the last assertion or the last probe
    start, or a human unblocked this card after the newest assertion ("try again now").
- **The probe's context** says it is the probe for X: check whether the wall still holds, and if it does,
  end again with the same `blocker_id`. Every worker's context lists the open blockers with their parked-card
  counts and the newest reason.
- **Clearing.** A probe run that ends without X (it completed, handed off, or stopped for some other reason)
  clears X. On the next dispatcher tick, cards still `blocked` behind X are unblocked automatically. Each gets a
  `shared_blocker_cleared` event and a dispatcher comment naming the clearing card and run.
  `hermes kanban dispatch` prints "Released (shared blocker cleared): ..." (`shared_blocker_released` in
  `--json`). Held `ready` cards simply stop being guarded.
- **Neutral outcomes** (`crashed`, `timed_out`, `reclaimed`, `spawn_failed`, `rate_limited`, `gave_up`,
  `released`) say nothing about the wall, so they neither assert nor clear it.
- **No triage escalation for probes.** A probe re-blocking on the blocker it was parked behind would normally
  count toward the unblock-loop limit and go to triage. Here the card stays `blocked`, its recurrence count is
  kept, and the event carries `shared_blocker_probe: true`.
- Only blockers asserted in the last 14 days are considered.

### How it works

No schema change: the blocker state is derived from `task_runs` history each time it is needed.

- `block_task(..., blocker_id=)` writes `{"blocker_id": X}` into the closed run's metadata and the `blocked`
  event payload. It synthesizes a run when there is none, so an id is never lost. `continue_task` puts it into
  the continuation run's metadata and the `continued` payload.
- `kanban_shared_blockers.blocker_states(conn)` scans runs whose metadata mentions `blocker_id`, then walks each
  of those cards' runs in order to find which cards are still behind which blocker (`behind`), when each was
  asserted and cleared, and when probe runs started. Cards that are `done`/`archived` are never behind anything.
- `shared_blocker_guard(conn, task_id)` is called from `check_respawn_guard` as step 2b, right after the
  quota/auth blocker check. It returns `shared_blocker` or `None`, and it fails open (an exception means
  no guard).
- `release_cleared_blockers(conn)` runs in `_run_reclaim_phase` after `recompute_ready` and unblocks blocked
  cards asserted before the clearing.
- `render_blocker_context` adds the "## Open shared blockers on this board" section to `build_worker_context`.
  It is advisory, so errors are swallowed.
- Matching on normalized error text was considered and left out: unrelated crash errors share fixed preambles,
  and block reasons written by an LLM never match verbatim.

### Code

```python
# hermes_cli/kanban_db_dispatch.py, check_respawn_guard
from hermes_cli.kanban_shared_blockers import shared_blocker_guard
try:
    shared = shared_blocker_guard(conn, task_id, now=now)
except Exception:
    shared = None
if shared is not None:
    return shared
```

```yaml
kanban:
  shared_blocker_probe_seconds: 1800   # 0 = no spacing, still one probe at a time
```

### Tests

`tests/hermes_cli/test_kanban_shared_blockers.py` (7 tests, real temp board, dispatcher with stubbed spawns):

- `block_task` records the id on the run and the event.
- Three unblocked cards behind an open blocker get exactly one spawn per tick, and the others are guarded
  `shared_blocker`.
- A probe that completes clears the blocker, and the cards still blocked behind it are unblocked without a
  human.
- Neutral outcomes neither clear nor assert.
- A board with no blocker has no guard.
- The `kanban_continue` tool carries the id into the run metadata, and the probe's worker context names it.
- The `kanban_block` tool normalizes and echoes the id and rejects a non-string one.

```sh
scripts/run_tests.sh tests/hermes_cli/test_kanban_shared_blockers.py
```

Passed on the full 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143 series at merge-base `4957bbd0a0` (Oct 19, 2026 PT). Untested: the 30-minute spacing
on a live board, and the human-unblock-lets-it-probe-now path (`_unblocked_since_assertion`), which no test
exercises.

### Applying

`git am -3 --empty=keep core/patches/0141-*.patch` after 0140 (both edit `tools/kanban_tools.py`). It also relies on
earlier fork Kanban patches: `kanban_continue` (`kanban-soft-deadline.md`, 0121), the block-kind routing and
unblock-loop triage (`kanban-block-loop-escalation.md`), and the `KANBAN_GUIDANCE` text from
`kanban-timed-waits.md`. Apply the series in order.

### Gotchas

- It works only if workers set `blocker_id`. A worker that blocks with prose only is not parked, and nothing
  infers the blocker from the reason text.
- Two workers that pick different slugs for the same wall make two blockers, each with its own probe. The
  "Open shared blockers" context lists existing ids to keep that rare.
- The probe has to say the wall is still there by passing the id again. A probe that blocks with some other
  reason clears the blocker and releases the parked cards. Usually that is right, because the wall is gone.
- State is recomputed from runs on every guard call. That is fine on normal boards, since the scan is bounded
  to 14 days and to runs whose metadata mentions `blocker_id`.

Plugin? No. The respawn guard, the block routing and worker-context assembly are dispatcher internals with no
hook points.

## Follow-up patch (1557): exact pins in blocker route previews

Patch: `patches/1557-fix-kanban-preserve-exact-pins-in-blocker-route-prev.patch` (`cd1354a43d3`, fix(kanban): preserve
exact pins in blocker route previews).

**Behavior.** For a card with an exact model/provider pin, the shared-blocker route preview now reports the pinned
route, the same one dispatch uses. Before, the preview applied provider-wall retiering to pinned cards, so a pinned
card on a walled provider could preview as some other provider and slip past (or wrongly fall under) that
provider's shared blocker.

**How it works.** `task_route` in `hermes_cli/kanban_shared_blockers.py` skips `retier_for_walls` when the tier
resolver's reason starts with `pinned` (`pinned`, `pinned+provider_resolved`, `pinned+provider_unresolved`). This
matches the condition `kanban_model_tier` already uses at dispatch. It is a one-line change.

**Tests.** `tests/hermes_cli/test_kanban_shared_blockers.py::test_pinned_wall_preview_matches_dispatch`, parametrized
over blocker provider `openai-codex` and `anthropic`. A card pinned to `openai-codex`, with a recorded
`openai-codex` wall, previews exactly as `apply_model_tier` routes it (`openai-codex`, lane `ready`). The guard
parks it only behind the `openai-codex` blocker. Untested: the `review` lane and legacy pins without a provider.

## Follow-up patches (1567–1568): shared-blocker sweeps fenced to the card's home gateway

Patches: `patches/1567-daveey-Fence-shared-blocker-sweeps-to-card-home-auth.patch` (`1991ea4a6d0`, Fence shared
blocker sweeps to card home authority); `patches/1568-daveey-Register-copied-board-fixture-without-importe.patch`
(`34f09d36616`, test-only fixture fix).

**Behavior.** When a card is synced to a worker node, both gateways hold a copy. Only the card's home gateway (its
`created_on`) writes the shared clear/release and stranded-probe election. Retained executor copies still run their
own host-local wake checks, and a wake check's clear evidence reaches home through sync. Provider scope is unchanged.

**How it works.**
- `release_cleared_blockers` skips ids in `kanban_wake_check.foreign_ids(conn)`, the cards in this file that another
  gateway created.
- `probe_stranded_blockers` still ranks every visible blocked candidate oldest-assertion first. If the winner is
  foreign, it stops (`break`) and leaves the winner's home to act; it does not move on to a local card. Filtering
  foreign cards out before the election would pick a different probe on each gateway.

**Tests.** `tests/hermes_cli/test_kanban_shared_blockers_copies.py` is a two-gateway test with real stores, pickup,
leases and sync over the in-memory Board transport (11 cases):
- `test_only_home_releases_shared_clear_before_sync`: lanes scheduled/blocked/ready/review × either order. The clear
  evidence comes from a real executor wake check. The worker releases nothing and writes no cleared event. Home
  releases only scheduled/blocked cards. After sync there is exactly one cleared event, plus one comment where a
  release happened.
- `test_only_home_elects_stranded_probe_before_sync` (either order): only home elects. After sync there is one
  probe-released event.
- `test_foreign_winner_is_not_filtered_before_election`: a foreign oldest winner on the worker is not replaced by a
  newer local card.

Patch 1568 only redefines the `gws` fixture locally by wrapping the imported one, so the module no longer shadows an
imported fixture name. The source owner reported the new cases RED on the old module and GREEN after, and 113
affected tests passing. I did not run them for this doc.

**Gotchas.** Election is consistent only if both gateways see the same candidate set. Ranking across gateways whose
visible card sets differ is not globally serialized and is not claimed. A file with no own gateway id has no foreign
cards (`foreign_ids` returns empty), so single-gateway boards behave as before.

**Applying.** 1557 and 1567–1568 depend on the earlier shared-blocker patches and the worker-node/wake-check series
(1568 needs `test_kanban_wake_check_home.py` and `test_kanban_worker_node.py`). Apply the full ordered series per the
README "Applying" section. Standalone application is not certified.
