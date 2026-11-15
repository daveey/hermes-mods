# Same-label pool backlog explanations and evidence bounds

Mirror snapshot 4142; ordinals 4072, 4073, 4074, 4075.

## Behavior

- The wait notice for a ready pool card that the origin machine cannot lease now reports same-label queue evidence: an approximate count of ready cards with exactly the same labels ahead of it, and the number of distinct same-label cards claimed in the last 30 minutes by currently available matching machines, with their names. The text states the scope is this board view and gives no pickup-time guarantee.
- When recent usable claims exist, the notice says "Not a placement problem, do not re-place" instead of recommending re-placement, and records the admission as reason `matching_pool_busy`, status `queued`. The stored payload carries a `label_backlog` object. A pending core-update hold still takes precedence over this reason.
- The queue-position description for a machine-lane card appends the same evidence, and when claims are recent it says the card is queued behind same-label cards.
- Moving a ready card is refused with reason code `capacity_wait` when the move drops a `capability:` label and usable recent claims contradict a placement fault. With `force`, the move proceeds under the existing gates and the moved audit event records the capacity evidence (first 500 characters) as `capacity_wait_overridden`.
- Evidence is only the current ready episode, capped at 30 minutes: claims before the card last became ready are ignored. A shared-blocker wake that moves the card to ready starts a new window. A shared-blocker-cleared event on an already-ready card that carries no ready status does not.
- Unreadable, ambiguous or missing evidence stays "unknown" and can never refuse a move.
- Entry paths: the notice and move guard are tested through the Python functions `post_notice`, `describe`/`placement` and `move_queued`. The `hermes kanban move` command line and other callers of the move check are untested here.

## How it works

- 4072: New `hermes_cli/kanban_label_backlog.py`. `available` keeps machines that are active, not on hold, not freshly draining, and whose effective labels contain the wanted labels. `read` takes the ready lane in canonical dispatch order (including donated priority), counts exact-label ready cards ahead of the task, and counts distinct tasks that have a `claimed` event in the window with a holder in the available set. A claim is excluded if the task's current labels differ from the wanted labels, the holder is missing, or the task has a later `moved` or `edited` event. A SQLite error returns the unknown result. `busy` is true only for known evidence with at least one recent claim. `words` formats the sentence. `move_refusal` applies only to a ready card with previous labels whose new labels drop a `capability:` label. `kanban_move.check` calls it, raises `MoveRefused("capacity_wait", ...)` unless forced, and adds the override to the audit host data. `kanban_queue_position.placement` attaches the backlog to a machine-lane place; `kanban_parent_unclaimed` includes it in the notice options and payload.
- 4073: Placement only stores the backlog when it has cards ahead or is busy, so empty placement shapes are preserved. The change adds tests for unknown evidence, label and holder parsing, and hold precedence.
- 4074: `read` clips the window start to `max(now - 30 min, ready_since)`, so a claimant from before the current wait does not count.
- 4075: `read` also advances the window start to the time of any `shared_blocker_cleared` event whose payload status is `ready`, locally to this guard; the shared notice-age clock is unchanged.

## Code

Source paths touched by this feature group (including regression fixtures):

- `hermes_cli/kanban_label_backlog.py`
- `hermes_cli/kanban_move.py`
- `hermes_cli/kanban_parent_unclaimed.py`
- `hermes_cli/kanban_queue_position.py`
- `tests/hermes_cli/test_kanban_label_backlog_wait.py`

Mail entries in the ordered series:

- 4072: [`5fa17f4cfdefb1c441c222b772e59a0ab08f2212`](patches/4072-daveey-explain-same-label-pool-backlog-before-capabi.patch)
- 4073: [`440e51e7a8bffc760d797bedbc662409cd93f139`](patches/4073-daveey-preserve-empty-placement-shapes-and-prove-bac.patch)
- 4074: [`a919870204c4b09c50ac821bba176c4f035f6dc2`](patches/4074-daveey-limit-pool-claim-evidence-to-current-ready-ep.patch)
- 4075: [`400efdbee178738be01d30ef2e515230592f094e`](patches/4075-daveey-bound-pool-evidence-after-shared-blocker-wake.patch)

## Tests

All in `tests/hermes_cli/test_kanban_label_backlog_wait.py`:
- `test_busy_pool_notice_reports_queue_place_and_does_not_advise_re_placement` (4072): two ahead, three recent claims by two workers, "Not a placement problem" text, `matching_pool_busy`/`queued` admission, payload counts, queue-position description.
- `test_no_recent_same_label_claims_keeps_the_capacity_advice` (4072): without claims the queue place is shown and the old re-place advice remains.
- `test_move_dropping_a_capability_while_matching_workers_claim_same_label_cards_is_refused` (4072): refusal `capacity_wait`, runs_on unchanged, forced move succeeds and the moved event holds `capacity_wait_overridden`.
- `test_move_dropping_a_capability_with_no_same_label_claims_is_allowed` (4072).
- `test_ambiguous_or_nonmatching_claim_is_not_capacity_evidence` (4072): old, future, wrong-holder, local-claim, extra-label, moved and edited cases count as zero and do not refuse.
- `test_unavailable_matching_workers_do_not_override_current_holds` (4072): held, draining, inactive or wrong-label workers give no evidence and no busy admission.
- `test_claim_duplicates_count_once_and_priority_donation_orders_queue` (4072).
- `test_move_protection_only_for_ready_capability_drops` (4072): running, blocked, no previous labels, unchanged labels and a drop that keeps the capability are not refused.
- `test_unknown_evidence_cannot_refuse_a_move`, `test_malformed_labels_are_not_evidence`, `test_malformed_holders_are_not_evidence`, `test_update_hold_takes_precedence_over_recent_pool_claims` (4073).
- `test_claims_before_current_ready_episode_do_not_veto_capability_drop` (4074, extended in 4075): for `continued` and a real shared-blocker wake, earlier claims count zero and the move is allowed; for a clear event on an already-ready card without status, the claim still counts and the move is refused; a claim at the current time refuses.

Tests were not rerun by this documentation pass. Assertions above describe the staged diffs, not fresh execution results.

## Applying

Follow the full ordered series in [README Applying](README.md#applying), including historical bridges, ancestry/replay handling and the mandatory hostname escape repair plus repaired-tree verification for the current snapshot. These mail entries are not independent cherry-picks.

## Gotchas

- Counts are approximate: admission may skip a predecessor, and the evidence covers this board view, not fleet capacity or pickup time.
- Claim evidence depends on labels as they are now; cards with a later move or edit are excluded rather than guessed at.
- The refusal only protects ready cards dropping `capability:` labels; other moves keep existing checks. `force` bypasses it.
- A bounded window of at most 30 minutes applies even to long-ready cards.
- No deployment claim is made here; the commit messages mention live-row replay and wider consumer suites as still to be verified.

## Plugin?

No. The change sits in core queue placement, notice generation and the move guard, which are not plugin hook points.
