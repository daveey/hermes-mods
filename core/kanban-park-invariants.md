# Kanban park invariants: a card never sits parked without showing who it waits on

Patches:

- `patches/0169-fix-kanban-cards-never-park-forever-without-showing-.patch`: parts 1-4 below (part 5 is the env scrub,
  documented in [top-level-env-scrub.md](top-level-env-scrub.md)).
- Patch **0280**, fork commit `ca145ec36e04a7db3271e2df91622dc7adbd91a2`:
  profile-scoped GitHub broker execution for PR acceptance.

Root cause: a board with cards "not running, not blocked". Three separate holes let a card park with nothing on the
board saying why: the respawn guard held `ready` cards forever, a closed-unmerged PR binding could never be replaced,
and `scheduled` parks could carry no wake time (or hide a question for a human).

## Behavior

1. **Respawn-guard hold cap.** A card the guard holds in `ready` never runs, so `consecutive_failures` never grows and
   the failure breaker cannot trip. Now the dispatcher blocks the card with the guard reason once the hold exceeds a cap:
   - `blocker_auth`: 30 min, blocked as `capability`;
   - every other guard reason: 6 h, blocked as `needs_input`;
   - `shared_blocker`: exempt (a declared wall another card is probing).
   The hold clock is the unbroken run of `respawn_guarded` events since the newest event of any other kind (claim,
   block, unblock, comment), so a real run restarts it. Tunables: `HERMES_KANBAN_GUARD_HOLD_CAP_SECONDS`,
   `HERMES_KANBAN_GUARD_HOLD_CAP_LONG_SECONDS`.
   A completion-gate refusal (`last_failure_error` starting `PR acceptance `) is never `blocker_auth`, whatever words
   its detail holds; a real provider auth failure still guards.
2. **PR acceptance.** A PR that is closed without merging gets classification `closed_unmerged` and a detail that
   names the way out (complete with `metadata.published_pr=<new PR>`). The catch-all `infra` detail names the failed
   phase and exception type instead of "check gh authentication" (that wording used to match the guard's auth regex).
   A binding to a closed-unmerged PR may be replaced by a new `published_pr` in the same repo (`pr_rebound` event);
   rebinding happens only on GitHub's positive "closed, not merged" answer, and never while the bound PR is open.
   Patch 0280 adds optional per-profile `kanban.pr_acceptance.gh_command_prefix`
   (default `[]`): an argv prefix, not a shell command. Routed profiles do not
   inherit the launch profile's wrapper. Missing `gh`, invalid wrapper values and
   unreadable configuration return a sanitized `setup` rejection; HTTP auth
   refusals remain `auth`, without captured stderr in durable receipts.
3. **Scheduled wake invariant.** Every `kanban_schedule` park carries `wake_at` (the `SCHEDULED_UNTIL=` marker, else
   now + 24 h). A card awaiting a human answer (`needs_input`/`capability` with an unanswered block) cannot be
   scheduled; the tool says it stays blocked. For legacy wake-less parks the dispatcher wakes the card at its implied
   time (marker in the reason, else park + 24 h) and moves human-kind ones straight to `blocked`
   (event field `wake_invariant`). New diagnostic `scheduled_without_wake` (error when human-kind or overdue).
4. **Escalations count as waiting on you.** A block-loop escalation in `triage` keeps its human `block_kind`: new
   diagnostics `escalated_waiting_on_user` (error) and `waiting_behind_user` (a `todo` child whose parent needs the
   user); `board_stats` / `hermes kanban stats` report `waiting_on_user` and `waiting_on_user_ids`. A plain triage
   card awaiting a specifier is not counted.

## How it works

- `hermes_cli/kanban_db_dispatch.py`: `guard_hold_cap_seconds`, `_guard_hold_started_at`, `_is_completion_gate_error`,
  `_block_if_guard_hold_capped` (called from `_dispatch_lane_task` before the `respawn_guarded` event is appended;
  the card lands in `result.auto_blocked`).
- `hermes_cli/kanban_pr_acceptance.py`: `phase` tracking, `closed_unmerged`, `pr_closed_unmerged(url, assignee)`;
  `kanban_pr_acceptance_store.py` does the rebind.
  For 0280, effective configuration/interpolation is bound to the assignee's home
  and secret scope. The wrapper receives the isolated environment and resolved
  `gh` argv. `hermes_platform/resolver/github.py` searches child PATH, then
  `~/bin` and `~/.local/bin`. No credential is returned to the parent process.
- `hermes_cli/kanban_timed_park.py`: `scheduled_payload` always has `wake_at`; `implied_wake`.
  `hermes_cli/kanban_continuation.py`: `missing_wake`, `_enforce_wake_invariant`, called from `wake_due_continuations`.
  `hermes_cli/kanban_db.py`: `schedule_task` refuses via `_awaiting_human_answer`; `board_stats` adds the waiting
  fields; `task_graph_contexts` returns each neighbor's `block_kind`.
- `hermes_cli/kanban_diagnostics.py`: `_rule_scheduled_without_wake`, `_rule_escalated_waiting_on_user`,
  `_rule_waiting_behind_user`. `hermes_cli/kanban.py`: "Waiting on you" line in `kanban stats`.
  `tools/kanban_tools.py`: the `kanban_schedule` refusal text.

## Code

`hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_pr_acceptance.py`, `hermes_cli/kanban_pr_acceptance_store.py`,
`hermes_cli/kanban_timed_park.py`, `hermes_cli/kanban_continuation.py`, `hermes_cli/kanban_db.py`,
`hermes_cli/kanban_diagnostics.py`, `hermes_cli/kanban.py`, `tools/kanban_tools.py`.
Patch 0280 also changes `hermes_cli/config_defaults.py`, adds
`hermes_platform/resolver/github.py`, and documents configuration in
`website/docs/user-guide/features/kanban.md`.

## Tests

`tests/hermes_cli/test_kanban_park_invariants.py` (new): `test_completion_gate_refusal_is_never_blocker_auth`,
`test_provider_auth_failure_still_guards`, `test_guard_hold_is_capped_and_auto_blocks_with_reason`,
`test_guard_hold_clock_restarts_after_a_real_run`, `test_closed_unmerged_pr_gets_its_own_classification`,
`test_catch_all_failure_names_the_phase_not_gh_auth`, `test_rebind_allowed_when_bound_pr_closed_unmerged`,
`test_rebind_refused_while_bound_pr_open`, `test_schedule_without_marker_still_gets_a_wake`,
`test_human_wait_cannot_park_into_scheduled`, `test_dispatcher_wakes_legacy_scheduled_card_without_wake`,
`test_dispatcher_honours_marker_in_legacy_reason`, `test_human_kind_scheduled_without_wake_becomes_blocked`,
`test_diagnostic_flags_scheduled_card_without_wake`, `test_block_loop_escalation_counts_as_waiting_on_user`,
`test_plain_triage_card_is_not_waiting_on_user`. Adjusted expectations in `test_kanban_continuation.py`,
`test_kanban_db.py`, `test_kanban_diagnostics.py`, `test_kanban_timed_park.py`.
Untested: the `waiting_behind_user` diagnostic and the `kanban stats` "Waiting on you" line.

Historical verification (broker numbered 0276 in that snapshot) on integrated core `54716420ff7c58cf1ab34fbfb22ef9e878f87cd3`:
canonical `scripts/run_tests.sh tests/hermes_cli/test_kanban_pr_transport.py
tests/hermes_cli/test_kanban_pr_acceptance.py -j 2 --file-retries 0` passed
16 tests (11 transport, five acceptance). The existing PM interpreter ran in a
disposable clone with only runner scratch allocation redirected. Transport cases
exercise actual synthetic executable children: missing CLI, user-bin discovery,
child-only token vending, noninheritance, auth refusal sanitization, invalid argv,
malformed config and assignee-scoped interpolation. No real credentials, live
GitHub acceptance, dispatcher deployment or UI entry was exercised by this suite.

## Applying

Apply in series after [kanban-timed-waits.md](kanban-timed-waits.md) (0113 0153 0155), whose `pending_wake`,
`kanban_timed_park` and `wake_due_continuations` it extends, and after [kanban-block-loop-escalation.md](kanban-block-loop-escalation.md)
and [kanban-escalated-triage-red.md](kanban-escalated-triage-red.md) (0161). Needs the PR-acceptance gate
(`completion_contract`).
Current ordering: broker patch 0280 follows the preserved series through 0279.
The published baseline contains patches 0001–0292; Chrome patches 0293–0295
follow it and are not broker dependencies. Apply the ordered series, not an
isolated broker patch. Patch 0278 now covers CI review-comment poll routing.

Historical recovered-CI ordering used broker 0278 after 0277, baseline
0001–0284 and Chrome 0285–0287; 0276 covered CI poll routing in that snapshot.
The newer published merged topology moved the same broker commit to 0280;
its published patch bytes are unchanged.

Historical application receipt for core `54716420ff7c58cf1ab34fbfb22ef9e878f87cd3`:
the broker was numbered 0276 after the preserved series through 0275. Full ordered
279-patch application from merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`
passed; all five broker paths match the integrated core. The last three patches in that historical 279-patch snapshot
were the separate experimental Chrome feature, not broker dependencies.

## Gotchas

- A `scheduled` park no longer waits for a cron/human forever: with no marker it wakes after 24 h and the run re-checks.
- Hold caps block with the guard reason; after fixing the cause (credentials, quota, PR binding) `unblock` the card.
- `kanban_schedule` now fails on a card waiting on a human answer; use `kanban_block`.
- The broker prefix must start with an installed absolute executable path, exec
  the appended command, preserve its exit status and emit only that command's
  JSON on stdout. Use an already-approved noninteractive scope: reads time out
  after 30 seconds. Never put credentials in the argv/config or print/persist
  tokens. Ordinary profile-isolated login remains the empty-prefix default.

## Plugin?

No. Dispatcher, completion gate and diagnostics are kanban kernel code with no plugin hook.
