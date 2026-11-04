# Respawn-guard hold notices and active_pr release

Patches:

- `patches/0212-kanban-respawn-guard-posts-comment-Inbox-card-within.patch`: a held card gets one comment + Inbox card within 30 min.
- `patches/0225-fix-kanban-explicit-check-False-on-respawn-guard-not.patch`: explicit `check=False` on the notice subprocesses (ruff PLW1510).
- `patches/0227-refactor-kanban-respawn-guard-hold-notice-lives-in-k.patch`: notice code moves to `kanban_db_dispatch_guard_notice.py`.
- `patches/0241-batch-pin-utf-8-decoding-in-dispatch-guard-gh-call-5.patch`: pin utf-8 decoding of the `gh` call output.
- `patches/0248-fix-kanban-respawn-guard-releases-active_pr-once-PRs.patch`: `active_pr` hold lifts once every named PR is merged/closed; no Inbox card; one event per hold.
- `patches/0250-refactor-kanban-move-active_pr-step-hold-event-dedup.patch`: PR step and hold-event dedupe move into guard siblings (code-health caps).
- `patches/0251-kanban-guard-hold-notice-file-a-fix-card-for-default.patch`: the notice files a fix card for the default assignee instead of posting to the Inbox.
- `patches/0312-fix-kanban-release-rejected-merge-queue-PRs-to-repai.patch`: completed merge-queue rejections release repair workers, with the rejection reason quoted once on the card.

Extends `kanban-park-invariants.md` and `kanban-pr-unblock.md` (the respawn guard itself).

## Behavior

- The respawn guard holds a `ready` card instead of spawning a worker (for example `active_pr`: the card's comments
  name a PR that is still being worked on, so a second worker would open a duplicate). Before this change a card could
  sit held for hours with nothing said.
- Once a hold has lasted past `HERMES_KANBAN_GUARD_HOLD_NOTIFY_SECONDS` (default 30 min), the dispatcher posts ONE kanban
  comment naming the guard reason and, for `active_pr`, the PR and a one-line summary of its failing checks. It also
  files ONE fix card for the default assignee (0212 first posted an Inbox card; 0248 dropped it; 0251 replaced it with
  a Kanban card because an unread Inbox poster gets muted). The notice does not restart the hold clock: the hold cap
  still fires.
- The `active_pr` hold lifts when EVERY named PR is MERGED, CLOSED, or OPEN with a completed queue rejection.
  The newest `bb-land` marker must say `done: true` and either `state: rejected` or `exit: 2`. The card gets one
  comment quoting the rejection reason and directing repair/resubmission of the existing PR. Queued, malformed,
  incomplete or unknown evidence keeps the hold. Ordinary review comments do not supersede queue markers.
- The dispatcher logs one `respawn_guarded` event per unbroken hold per reason, not one per tick (about 1400 events per
  card before). A human comment breaks the hold, so the next held tick logs a fresh event.

## How it works

`hermes_cli/kanban_db_dispatch.py` calls `guard_hold_reason` to find the hold in progress (newest event, ignoring the
dispatcher's own logs), then `_notify_guard_hold` in `kanban_db_dispatch_guard_notice.py` posts the comment and files the fix card
once per hold. `kanban_db_dispatch_pr_state.py` looks up each PR with `gh pr view` (`pr_state`, cached: merged/closed
forever, open for 300 seconds). A newly observed rejection releases the card in that dispatch pass; a previously
cached OPEN result can delay observation up to 300 seconds. Rejections are not cached, so a requeue is seen on the
next lookup. `kanban_db_dispatch_pr_rejection.py` parses the newest marker and deduplicates repair comments.
`all_prs_finished(urls)` retains its terminal-GitHub-state semantics; the active guard handles rejection separately.

## Code

- `hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`.

## Tests

- `tests/hermes_cli/test_kanban_park_invariants.py`: one comment (and fix card) naming the PR and failing checks within 30
  min, hold clock not restarted, the event re-logged after a human comment, PR-state table (merged/closed/open/unknown),
  merged-PR cache, end-to-end "a ready card whose only PR merged is spawned, not held".
- `tests/hermes_cli/conftest.py`: keeps the PR-state lookup offline by default (`gh` "fails", so `active_pr` holds).
- `tests/hermes_cli/test_kanban_pr_rejection.py`: rejected-open dispatch, quoted reason once, newest queued marker,
  incomplete/malformed/truncated markers, exit-2 alternative, ordinary comment, merged control, and requeue without stale cache.
- Verified for 0312: 114 passing tests across rejection, park invariants, dispatch and both review lifecycle files;
  rejection regression observed failing before the fix. A real OPEN rejected PR response was fetched read-only and
  its rejection reason parsed. Production dispatcher deployment remains separate from these unit/parser proofs.

## Applying

`git am -3 --empty=keep` in order 0212, 0225, 0227, 0241, 0248, 0250, 0251 (the last four assume the `*_guard_notice` module from 0227).

## Five whys (0312)

Repair stalled because `active_pr` treated an OPEN rejected PR as active. The PR stayed open because queue rejection
ends the merge attempt, not the PR. The guard queried only GitHub lifecycle state, so it could not distinguish
queue ownership from repair ownership. Earlier merged/closed tests missed this state combination. The previous
terminal-state release fix therefore did not cover rejection. Countermeasures: model completed queue rejection,
quote its repair reason, and test the full dispatcher release plus queued/merged/malformed/requeue controls.

## Gotchas

- Needs `gh` authenticated on the dispatcher host; without it holds simply never lift early.
- Patch 0251 was authored on a branch without 0248/0250, so the linear series would not apply it; the mirrored 0251 is the conflict-resolved form (it yields the same files as the fork's `main`).
- The fix card goes to the `default` profile; a board without that profile would drop it.

## Plugin?

No. Dispatcher behavior inside core.

## Never-run cards (0459, 0339)

Patch **0459** (`f75d239f8b`): `active_pr` returns None when the card has no `task_runs` or every PR-naming comment
predates its first run (`_pr_comments_predate_first_run` in `kanban_db_dispatch_pr_state.py`): a worker that never
ran cannot have opened a duplicate PR, so a review card is not held on the PR it exists to review. A worker that
opened a PR then crashed is still held. Tests: `tests/hermes_cli/test_kanban_pr_continuation.py` (never-run and
crash cases) plus the `worker_ran` fixture in `tests/hermes_cli/kanban_guard_helpers.py`. Patch **0339** is a
mirror fix-up that syncs `test_kanban_pr_continuation.py` with the source after a merge-adaptation conflict.

Patch **0466** (`fb67f2fac2`) fixes 0459: the never-run release returned before the merge-queue rejection check, so a
never-run repair card whose handoff names a queue-rejected PR lost its "repair and resubmit" note. The never-run
path now posts `comment_rejection` for every `REJECTED` PR in the handoff before releasing. Tests:
`tests/hermes_cli/test_kanban_pr_rejection.py` (worker-opened cases now mark `worker_ran`; the never-run case
still gets the note).

## Hold-cap clock and ready diagnostics (0790)

Patch **0790** (fork `48e6269b08`) makes `guard_hold_started_at` ignore comments from every author, not just the dispatcher. A human or orchestrator reply therefore cannot restart the hold-cap clock. A non-comment, non-`respawn_guarded` event still starts a new clock segment. This clock is separate from the event-deduplication behavior described above: a comment can cause another guard event without resetting the cap.

`hermes_cli/kanban_diagnostics.py` also reports an old, unclaimed, assigned ready card as `held_in_ready`, rather than `stranded_in_ready`, when its latest event is `respawn_guarded` or its `machine:*` pin names another machine. The usual age threshold still applies. A pin to the configured own machine is not a foreign-machine hold. The diagnostic uses the latest event literally: a later comment removes the respawn-guard classification unless a foreign pin still applies.

Tests:

- `tests/hermes_cli/test_kanban_quota_guard.py::test_hold_clock_survives_non_dispatcher_comments`: a non-dispatcher comment leaves the original start timestamp intact, and the cap blocks the card after the original deadline.
- `tests/hermes_cli/test_kanban_diagnostics.py::test_ready_hold_is_reported_apart_from_stranded`: guard and foreign-pin holds, own-machine control, later-comment control, and young-hold suppression.

Applying: append 0790 in series order; it changes the existing guard-notice helper and diagnostics module. A real dispatcher waiting through the cap and rendered diagnostic UI are untested here.

## Foreign pulls, disclaimers and portable comment provenance (1626 1636 1637 1653 1654 1655)

Patches:

- **1626** (`7d7c542ce7`, `fix(kanban): do not hold active_pr on another card's pull`): first form of the fix, authored on a
  side branch. Its pull-policy helpers (`_urls_that_hold`, `_holding_pr_comments` in `kanban_db_dispatch_pr_state.py`)
  are **not** in the frozen fork tip: 1653 is the port onto main and replaces them. 1626's guard-notice changes carry
  over unchanged in substance.
- **1636** (`c65a1f168a`, `fix(kanban): stamp portable writer_run_uid on worker comments so replicas keep active_pr`).
- **1637** (`ec1183c8ad`): refactor; moves the writer-provenance stamping into `kanban_db_comment_ids.writer_provenance`
  to keep `kanban_db.py` under its size cap. No behavior change.
- **1653** (`d3caa17dc5`): the port of 1626 onto main; the policy moves to the new
  `hermes_cli/kanban_db_dispatch_pr_policy.py`.
- **1654** (`09f7129119`, `keep mixed-publication disclaimers fail closed`) and **1655** (`092736e844`, `require an unambiguous
  pull disclaimer subject`): narrow what counts as a disclaimer.

### Behavior

- **Another card's pull does not hold.** Among this card's own or unknown-provenance comments, a pull URL that is
  another *live* card's `completion_contract` is not duplicate-PR evidence, so it no longer arms `active_pr`. Matching
  ignores case and a trailing `/`. A `done` or `archived` card's contract is not live and does not exempt the URL.
- **A disclaimer does not hold.** A pull named only in comments whose purpose is to say it does not block this card
  ("`<URL>` only changes wording. It doesn't block you.") does not hold. Every comment naming that URL must be such a
  disclaimer; a separate "Opened `<URL>`" comment still holds.
- **This card's own `completion_contract` always holds**, even when a comment says it does not block the card.
- **Disclaimers are narrow (1654, 1655).** A comment counts as a disclaimer only when it names exactly one pull URL
  (1655 counts raw matches, so the same pull written twice is two), contains none of `opened`/`created`/`published`/
  `pushed`, and has the URL as the subject of the phrase: `[See ]<URL> does not block this card.` or
  `<URL> <clause>. It doesn't block you.`, with nothing after it except `.`/`!`. A "doesn't block" phrase whose object is
  not the card (`you`, `this card/task`, `the card`), an unrelated subject ("The CI failure does not block this card"),
  or a mixed comment that also publishes a pull stays held.
- **Watermark.** Only comments that keep at least one holding pull advance `latest_pr_comment_id`, so a filtered mention
  cannot move the handoff ordering.
- **Hold notice reads checks like `pr_state` does.** `_pr_failing_checks` uses the same `gh` argv as `pr_state` (the
  token-broker prefix when configured). A successful read with no failures now says `0 failing checks`; only an
  unreadable read (non-zero exit with empty stdout, or a launch error/timeout) says `failing checks unavailable`.
- **Fix-card text.** The fix card no longer tells the dispatcher worker to unblock or schedule the held card (a worker
  cannot mutate another card and its shell is write-fenced). It says the dispatcher releases the hold on the next tick
  once the guard no longer matches; a manual lift is an orchestrator or CLI write.
- **Portable comment writer (1636, 1637).** `add_comment` stamps `writer_run_uid` (`task_runs.uid`) next to the
  database-local `writer_run_id` when the board has a uid column. Sync remaps `task_events.run_id` but not the JSON
  payload, so on a replica the integer could name another card's run and the owner's own PR comment was classed as a
  foreign FYI, dropping the `active_pr` hold (fail open). Now `_foreign_pr_comment_ids` and `own_comment_ids` (the live
  comment bridge) decide by the uid whenever the key is present, with **no** fallback to the integer when the uid is
  present but unusable (`None`, `""`, non-string, unknown): such a comment stays owned (fail closed). Uid-less legacy
  comments keep the integer behaviour.

### How it works

`active_pr_reason` (`kanban_db_dispatch_pr_state.py`) splits comments into foreign FYI (by `_foreign_pr_comment_ids`) and
candidates, then calls `kanban_db_dispatch_pr_policy.holding_urls(conn, task_id, [(url, body), ...])`. That groups
mentions by normalized URL, reads the card's own contract (`_own_contract_key`, only when it starts with `http`) and
queries `tasks` for other non-done/non-archived cards whose `lower(rtrim(completion_contract, '/'))` matches
(`_foreign_live_contract_keys`). A URL holds if it is the own contract, or it is not a foreign live contract and not
every body naming it passes `_says_pull_does_not_block`. Writer provenance lives in
`kanban_db_comment_ids.writer_provenance(conn, task_id)` (1637), which returns `{writer_run_id, writer_run_uid?}` when
`HERMES_KANBAN_TASK`/`HERMES_KANBAN_RUN_ID` name this card's run, and `own_comment_ids` matches by uid when the run has
one (SQL `json_type(payload, '$.writer_run_uid')`).

### Code

`hermes_cli/kanban_db_dispatch_pr_policy.py` (new in 1653), `hermes_cli/kanban_db_dispatch_pr_state.py`,
`hermes_cli/kanban_db_dispatch_guard_notice.py`, `hermes_cli/kanban_db.py`, `hermes_cli/kanban_db_comment_ids.py`.

### Tests

Named from the patches; **not rerun by this mirror pass**.

- `tests/hermes_cli/test_kanban_active_pr_foreign_contract.py` at its 1655 state (1626 created it; 1653 replaces its
  content): `test_own_comment_that_only_names_another_live_cards_contract_does_not_hold`,
  `test_own_nonblocking_comment_alone_does_not_hold`, `test_foreign_contract_match_ignores_case_and_trailing_slash`,
  `test_own_pull_url_still_holds`, `test_own_contract_holds_even_when_comment_says_it_does_not_block`,
  `test_done_cards_contract_does_not_exempt`, `test_foreign_url_does_not_drop_an_own_url_in_the_same_comment`,
  `test_nonblocking_comment_does_not_exempt_a_real_open_comment`, `test_unknown_provenance_naming_another_pull_still_holds`,
  `test_unknown_provenance_naming_only_a_foreign_contract_does_not_hold`, `test_fyi_mention_does_not_undo_an_own_disclaimer`,
  `test_mixed_own_foreign_contract_nonblocking_and_fyi_only_own_pull_is_consulted`,
  `test_filtered_comment_does_not_advance_latest_pr_comment_id`; notice cases
  `test_notice_for_broker_readable_zero_failing_does_not_say_unavailable`, `test_notice_reports_failing_checks_through_broker`,
  `test_notice_unreadable_checks_still_say_unavailable`, `test_notice_gh_launch_failure_says_unavailable`,
  `test_fix_card_does_not_tell_worker_to_unblock_or_schedule`; 1654 `test_mixed_comment_disclaimer_does_not_drop_published_pull`
  (2 cases); 1655 `test_publication_or_unrelated_subject_is_not_a_pull_disclaimer` (5 phrasings).
- `tests/hermes_cli/test_kanban_pr_comment_writer_uid_sync.py` (1636, real sync outbox/apply with an offset run-id
  namespace): `test_worker_comment_stamps_run_uid`, `test_synced_owner_pr_comment_keeps_active_pr`,
  `test_live_bridge_own_ids_resolve_by_uid_on_replica`, `test_foreign_card_worker_comment_with_real_uid_stays_fyi`,
  `test_foreign_uid_wins_over_owning_integer`, `test_present_invalid_uid_never_falls_back_to_foreign_integer`
  (`None`, `""`, `42`, unknown), `test_legacy_uidless_comment_keeps_integer_behaviour`, `test_unattributed_destination_comment_is_fyi`,
  `test_worker_comment_helper_matches_tool_provenance`.
- `test_kanban_dependency_ordering_sync.py` and `test_kanban_dependency_review_sync.py`: their transports no longer
  re-add the PR comment on the destination; the synced comment now keeps its writer by uid.

Untested: a live `gh`/token-broker read (the argv is asserted with a fake `subprocess.run`), the CLI `hermes kanban
comment` path stamping the uid (the tests write through `kb.add_comment` with the worker env set), and English
phrasings beyond the listed fixtures.

### Applying

Apply in series order. 1626 and 1653 both create `tests/hermes_cli/test_kanban_active_pr_foreign_contract.py` and edit
the same guard-notice hunks; 1626 reached main through a merged branch and 1653 is its port, so a linear replay needs the
mirror's replay handling (see the verification report for this snapshot). 1637 depends on 1636; 1654 and 1655 edit the
module 1653 adds.

### Gotchas

- The disclaimer grammar is deliberately English-only and narrow: a genuinely informational mention phrased any other
  way keeps the hold (fail closed). Name another card's pull in that card's `completion_contract` instead.
- A pull that was another card's contract stops being exempt as soon as that card is done or archived.
- 1636 makes the 1610 note in [kanban-pr-park-handoff.md](kanban-pr-park-handoff.md) (fixtures re-adding an unattributed
  PR comment) obsolete; the synced comment keeps its writer by uid.


## Follow-up 1901, 1902: a supported reopen after success reaches normal dispatch

Patches: **1901** (`3c624f4f9f`, `[daveey] kanban: a supported reopen after success reaches normal dispatch (recent_success/active_pr)`), **1902** (`5698335772`, `[daveey] kanban reopen guard: pin same-second active_pr tie; docstring names the rerun bound`). Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

### Behavior

- A supported supervisor/operator `reopened` event with `previous_status=done`, strictly later than a completed run or the latest owned PR comment, lifts the `recent_success`/`active_pr` holds; same-second, older, malformed and unrelated events stay guarded (1901/1902).
- The reopen authorizes reruns until one completes or posts a fresh PR comment, not a single spawn; reclaimed attempts follow ordinary retry rules (1902).

### How it works

`hermes_cli/kanban_db_dispatch_rerun.py` decides whether a reopen authorizes a rerun; `kanban_db_dispatch.py`, `kanban_db_dispatch_pr_state.py` and `kanban_db_operator.py` consult it. Reopen reasons that mention the PR are recorded as `commented` FYI provenance and do not count as a fresh publication.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1901: `hermes kanban reopen` (reopen_done_task) emits `reopened`, which the respawn guard's recent_success step did not count as a deliberate re-run (only status/promoted/unblocked/ reclaimed), so a card reopened right after completing sat held for the 1 h window.
- 1902: Review of #808: the strict reopen tie was only killed through recent_success; add an active_pr-only test (success window over, PR comment and reopen in one second stay guarded). Docstring: a reopen authorizes reruns until one completes or posts a PR comment; a rerun ending without either retries like any reclaimed run.

### Code

`hermes_cli/kanban_db_dispatch.py`, `hermes_cli/kanban_db_dispatch_pr_state.py`, `hermes_cli/kanban_db_dispatch_rerun.py`, `hermes_cli/kanban_db_operator.py`.

### Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/hermes_cli/test_kanban_reopen_dispatch.py`: `test_cli_reopen_after_success_dispatches_once`, `test_dashboard_done_to_ready_still_dispatches`, `test_reopen_authorizes_one_rerun_only`, `test_same_second_reopen_stays_guarded`, `test_unrelated_or_malformed_events_stay_guarded`, `test_open_parent_still_gates_reopened_child`, `test_pr_owner_reopen_resumes_its_pr_once`, `test_reopen_without_newer_reopen_keeps_pr_guard`, `test_fenced_worker_shell_cannot_reopen`, `test_same_second_reopen_keeps_pr_guard`
