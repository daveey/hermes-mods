# fix(kanban): never dispatch a card this machine cannot lease; pick up pinned reviews [daveey]

Patch 0862, source `c44335f7746592fa03560621d9904024626898d0`.

## Behavior

fix(kanban): never dispatch a card this machine cannot lease; pick up pinned reviews [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `class GatewayKanbanWatchersMixin:`
- `class DispatchResult:`
- `def describe_suppression(results: Iterable[Optional[DispatchResult]]) -> str:`
- `def dispatch_profile_allowlist_summary() -> str:`
- `def _dispatch_lane_task(`
- `def _dispatch_once_locked(`
- `class ClaimGate:`
- `def claim_gate(conn: sqlite3.Connection, task_id: str, expires: int, lane: str =`
- `def _cmd_daemon(args: argparse.Namespace) -> int:`
- `def evict(conn: sqlite3.Connection) -> list[str]:`
- `def pickup(conn: sqlite3.Connection, *, budget: Optional[int],`
- `+"""Local dispatch applies the Board's runs_on / label eligibility before it claims (MacBook stall, 2026-10-28).`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> The MacBook dispatcher stalled 2026-10-28 16:35-17:08 PDT: one spawn in 33 min while the ready queue grew to 15
> with 5/6 slots used. Every tick deferred t_01a184c7144d5773 (status review, runs_on ["machine:hub"]) with
> claim_deferred: labels_mismatch.
>
> Why chain (confirmed in code):
> - Why 1: each tick spent its spawn on t_01a184c7144d5773 and the Board refused the lease (labels_mismatch):
>   kanban_db_sync.claim_gate via claim_review_task.
> - Why 2: local candidate selection (lane_rows for ready and review, has_spawnable_*) never applied runs_on
>   eligibility; it existed only in kanban_worker_node.eligible (pickup) and in the Board's grantLease.
> - Why 3 (corrected): not "a deferred claim ends the tick" (the lane loops already continue), but the review
>   reservation: _any_spawnable_review saw that card as spawnable review work, so with one free slot
>   ready_budget = 0, the ready lane started nothing, and the review claim was deferred. The reserved slot was
>   never given back to ready work.
> - Why 4: the card sat in review forever because worker-node pickup copied only 'ready' cards, so the hub never
>   took the review of a card pinned to it, and after its implementation run the copy was evicted.
> - Root (happened): runs_on eligibility was enforced only by the remote lease, after local selection, and
>   pickup had no review lane.
> - Root (not caught): no test put a runs_on-ineligible card ahead of eligible work; the stuck detector had no
>   "deferred claims" reason and blamed profile health.
>
> Fix:
> - kanban_worker_node.local_refusals: the Board's refusal (eligible()) for unclaimed cards of a lane, on primary
>   boards; registry unreachable = no local filtering (the lease still fails closed). Used by the ready and
>   review lanes, the review reservation and has_spawnable_ready/review. Refused cards are counted in
>   DispatchResult.skipped_ineligible and never listed by --dry-run.
> - A deferred claim is recorded (DispatchResult.claim_deferred, reason from claim_gate) and the tick moves on;
>   a review-reserved slot no review took goes back to the remaining ready rows in the same tick.
> - Worker-node pickup also takes foreign review cards whose runs_on names this machine (explicit runs_on only,
>   only when review dispatch is on), so the pinned machine reviews them.
> - stuck_warning(): the CLI daemon and the gateway's "dispatcher stuck" line name claim_deferred(<reason>) /
>   skipped_ineligible(<reason>) and point at runs_on and Board leases instead of profile health.
>
> Tests: tests/hermes_cli/test_kanban_runs_on_dispatch.py (acceptance: one free slot, review card pinned to
> another machine ahead of an eligible ready card, the ready card spawns that tick; fails on 131f0a3467 with
> spawned == []). Kanban dispatch/sync/worker-node suites: 27 files, 326 tests passed.

## Code

- `gateway/kanban_watchers.py`
- `hermes_cli/kanban_db_dispatch.py`
- `hermes_cli/kanban_db_sync.py`
- `hermes_cli/kanban_ops.py`
- `hermes_cli/kanban_worker_node.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_kanban_runs_on_dispatch.py`

New or changed test definitions present in the source diff:

- `test_an_ineligible_review_card_does_not_hold_the_last_slot`
- `test_dry_run_never_lists_a_card_this_machine_cannot_lease`
- `test_health_probe_does_not_count_ineligible_cards`
- `test_the_pinned_machine_picks_up_and_reviews_the_card`
- `test_a_deferred_review_claim_hands_its_slot_to_ready_work`
- `test_stuck_warning_names_deferred_claims_not_profile_health`
- `test_unsynced_boards_are_unaffected`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
