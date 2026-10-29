# Kanban park invariants: a card never sits parked without showing who it waits on

Patches:

- `patches/0169-fix-kanban-cards-never-park-forever-without-showing-.patch`: parts 1-4 below (part 5 is the env scrub,
  documented in [top-level-env-scrub.md](top-level-env-scrub.md)).
- Patch **0335**, fork commit `ca145ec36e04a7db3271e2df91622dc7adbd91a2`:
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
   Patch 0335 adds optional per-profile `kanban.pr_acceptance.gh_command_prefix`
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
  For 0335, effective configuration/interpolation is bound to the assignee's home
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
Patch 0335 also changes `hermes_cli/config_defaults.py`, adds
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

Historical verification (broker numbered 0312 in that snapshot) on integrated core `54716420ff7c58cf1ab34fbfb22ef9e878f87cd3`:
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
Current ordering: broker patch 0335 follows the preserved series through 0331.
The published baseline contains patches 0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148, 0149, 0150, 0151, 0152, 0153, 0154, 0155, 0156, 0157, 0158, 0159, 0160, 0161, 0162, 0163, 0164, 0165, 0166, 0167, 0168, 0169, 0170, 0171, 0172, 0173, 0174, 0175, 0176, 0177, 0178, 0179, 0180, 0181, 0182, 0183, 0184, 0185, 0186, 0187, 0188, 0189, 0190, 0191, 0192, 0193, 0194, 0195, 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207, 0208, 0209, 0210, 0211, 0212, 0213, 0214, 0215, 0216, 0217, 0218, 0219, 0220, 0221, 0222, 0223, 0224, 0225, 0226, 0227, 0228, 0229, 0230, 0231, 0232, 0233, 0234, 0235, 0236, 0237, 0238, 0239, 0240, 0241, 0242, 0243, 0244, 0245, 0246, 0247, 0248, 0249, 0250, 0251, 0252, 0253, 0254, 0255, 0256, 0257, 0258, 0259, 0260, 0261, 0262, 0263, 0264, 0265, 0266, 0267, 0268, 0269, 0270, 0271, 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286, 0287, 0288, 0289, 0290, 0291, 0292, 0293, 0294, 0295, 0296, 0297, 0298, 0299, 0300, 0301, 0302, 0303, 0304, 0305, 0306, 0307, 0308, 0309, 0310, 0311, 0312, 0313, 0314, 0315, 0316, 0317, 0318, 0319, 0320, 0321, 0322, 0323, 0324, 0325, 0326, 0327, 0328, 0329, 0330, 0331, 0332, 0333, 0334, 0335, 0336, 0337, 0338, 0339, 0340, 0341, 0342, 0344, 0345, 0346, 0347, 0348, 0349, 0350, 0351, 0352, 0353, 0354, 0355, 0356, 0357, 0358, 0359, 0360, 0362; Chrome patches 0375, 0376, 0377
follow it and are not broker dependencies. Apply the ordered series, not an
isolated broker patch. Patch 0330 now covers CI review-comment poll routing.

Historical recovered-CI ordering used broker 0330 after 0329, baseline
0001, 0002, 0003, 0004, 0005, 0006, 0007, 0008, 0009, 0010, 0011, 0012, 0013, 0014, 0015, 0016, 0017, 0018, 0019, 0020, 0021, 0022, 0023, 0024, 0025, 0026, 0027, 0028, 0029, 0030, 0031, 0032, 0033, 0034, 0035, 0036, 0037, 0038, 0039, 0040, 0041, 0042, 0043, 0044, 0045, 0046, 0047, 0048, 0049, 0050, 0051, 0052, 0053, 0054, 0055, 0056, 0057, 0058, 0059, 0060, 0061, 0062, 0063, 0064, 0065, 0066, 0067, 0068, 0069, 0070, 0071, 0072, 0073, 0074, 0075, 0076, 0077, 0078, 0079, 0080, 0081, 0082, 0083, 0084, 0085, 0086, 0087, 0088, 0089, 0090, 0091, 0092, 0093, 0094, 0095, 0096, 0097, 0098, 0099, 0100, 0101, 0102, 0103, 0104, 0105, 0106, 0107, 0108, 0109, 0110, 0111, 0112, 0113, 0114, 0115, 0116, 0117, 0118, 0119, 0120, 0121, 0122, 0123, 0124, 0125, 0126, 0127, 0128, 0129, 0130, 0131, 0132, 0133, 0134, 0135, 0136, 0137, 0138, 0139, 0140, 0141, 0142, 0143, 0144, 0145, 0146, 0147, 0148, 0149, 0150, 0151, 0152, 0153, 0154, 0155, 0156, 0157, 0158, 0159, 0160, 0161, 0162, 0163, 0164, 0165, 0166, 0167, 0168, 0169, 0170, 0171, 0172, 0173, 0174, 0175, 0176, 0177, 0178, 0179, 0180, 0181, 0182, 0183, 0184, 0185, 0186, 0187, 0188, 0189, 0190, 0191, 0192, 0193, 0194, 0195, 0196, 0197, 0198, 0199, 0200, 0201, 0202, 0203, 0204, 0205, 0206, 0207, 0208, 0209, 0210, 0211, 0212, 0213, 0214, 0215, 0216, 0217, 0218, 0219, 0220, 0221, 0222, 0223, 0224, 0225, 0226, 0227, 0228, 0229, 0230, 0231, 0232, 0233, 0234, 0235, 0236, 0237, 0238, 0239, 0240, 0241, 0242, 0243, 0244, 0245, 0246, 0247, 0248, 0249, 0250, 0251, 0252, 0253, 0254, 0255, 0256, 0257, 0258, 0259, 0260, 0261, 0262, 0263, 0264, 0265, 0266, 0267, 0268, 0269, 0270, 0271, 0272, 0273, 0274, 0275, 0276, 0277, 0278, 0279, 0280, 0281, 0282, 0283, 0284, 0285, 0286, 0287, 0288, 0289, 0290, 0291, 0292, 0293, 0294, 0295, 0296, 0297, 0298, 0299, 0300, 0301, 0302, 0303, 0304, 0305, 0306, 0307, 0308, 0309, 0310, 0311, 0312, 0313, 0314, 0315, 0316, 0317, 0318, 0319, 0320, 0321, 0322, 0323, 0324, 0325, 0326, 0327, 0328, 0329, 0330, 0331, 0332, 0333, 0334, 0335, 0336, 0337, 0338, 0339, 0340, 0341, 0342, 0344, 0345, 0346, 0347, 0348, 0349, 0350, 0351 and Chrome 0352, 0353, 0354; 0312 covered CI poll routing in that snapshot.
The newer published merged topology moved the same broker commit to 0335;
its published patch bytes are unchanged.

Historical application receipt for core `54716420ff7c58cf1ab34fbfb22ef9e878f87cd3`:
the broker was numbered 0312 after the preserved series through 0306. Full ordered
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
