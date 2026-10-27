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

## Never-run cards (0453, 0339)

Patch **0453** (`f75d239f8b`): `active_pr` returns None when the card has no `task_runs` or every PR-naming comment
predates its first run (`_pr_comments_predate_first_run` in `kanban_db_dispatch_pr_state.py`): a worker that never
ran cannot have opened a duplicate PR, so a review card is not held on the PR it exists to review. A worker that
opened a PR then crashed is still held. Tests: `tests/hermes_cli/test_kanban_pr_continuation.py` (never-run and
crash cases) plus the `worker_ran` fixture in `tests/hermes_cli/kanban_guard_helpers.py`. Patch **0339** is a
mirror fix-up that syncs `test_kanban_pr_continuation.py` with the source after a merge-adaptation conflict.

Patch **0460** (`fb67f2fac2`) fixes 0453: the never-run release returned before the merge-queue rejection check, so a
never-run repair card whose handoff names a queue-rejected PR lost its "repair and resubmit" note. The never-run
path now posts `comment_rejection` for every `REJECTED` PR in the handoff before releasing. Tests:
`tests/hermes_cli/test_kanban_pr_rejection.py` (worker-opened cases now mark `worker_ran`; the never-run case
still gets the note).
