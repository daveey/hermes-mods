# Kanban review: author custody across rework and downstream reviews

Mirror snapshot 5406; ordinals 5316, 5317, 5318, 5319, 5320, 5321.

Entries:

- **5316**: Keep full author history for source cards behind downstream reviews
- **5317**: test: model review parking resuming to review phase after rebase onto main
- **5318**: refactor: split no-route refusal out of resolve_review_model for the health cap
- **5319**: Keep original author when a verdict separates identical immutable re-handoffs
- **5320**: fix(kanban): retain artifact-keyed author custody across rework runs
- **5321**: fix(kanban): retain full authorship for unproven replacement artifacts

## Behavior

- Review-model selection excludes models that authored the work under review. These entries tighten how long an author stays excluded.
- **5316**: a downstream review card (one whose source/creator is the implementation card) now reads the *full* author history of its source cards. The exact-head diff holds every epoch's commits, so only the card's own lane stays bounded to authors since the last verdict. `_current_authors` gains a `bounded` flag that source recursion turns off.
- **5317**: test-only; models a review parking and resuming to the review phase after a rebase.
- **5318**: refactor-only; the no-route refusal is split out of `resolve_review_model` so the health cap on that function is met.
- **5319**: a review-lane run is not work on the artifact, so it no longer resets `rehandoff_runs`; an unchanged merged artifact re-handed off after `changes_requested` keeps its first author excluded.
- **5320**: author custody is keyed to delivered artifact identities (a concrete PR head), not verdict boundaries. Authors are retained through continued, crashed and no-spawn rework and A-to-B-to-A returns. A complete `review_commit_range` receipt lets earlier authors be released only for disjoint commit sets; a missing range receipt conservatively retains earlier heads of the same PR. Open and merged head identities are supported; artifact-less historical handoffs keep the legacy fallback.
- **5321**: a replacement PR identity, or range commits that cannot be attributed, can no longer release historical implementers; the selector falls back to full implementation history while still honouring review-run and proven no-code re-handoff classification. Missing ranges stay fail-closed; complete attributable same-PR disjoint ranges keep narrowing.

## How it works

- `kanban_review_model.implementer_models` walks the task and its source cards; `_current_authors(conn, task_id, bounded)` derives the epoch's authors from run/event history.
- `kanban_review_artifacts.py` (new in this series' shipped paths) maps runs to artifact identities and commit-range receipts and decides when an earlier author may be released.
- `kanban_review_retry.py` keeps the re-handoff counter honest when a verdict separates otherwise identical immutable re-handoffs.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_review_model.py`
- `hermes_cli/kanban_review_retry.py`
- `hermes_cli/kanban_review_artifacts.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here; any figures quoted above are producer-reported):

- `tests/hermes_cli/test_kanban_review_model_cycles.py`
- `tests/hermes_cli/test_kanban_review_retry_custody.py`
- `tests/hermes_cli/test_kanban_review_model_artifacts.py`
- `tests/hermes_cli/test_review_artifact_independent_probe.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- The direction of every change is toward *more* exclusion when evidence is missing; narrowing needs complete, attributable range receipts.
- Historical handoffs with no artifact record keep the legacy fallback, so old boards behave as before.
- Producer-reported verification (not re-run here) quotes consumer suites of several hundred tests plus mutant checks; treat as producer evidence.

## Why core instead of a plugin

Review routing reads the kanban run/event store, task custody and model-route resolution, all of which live in core. A plugin has no hook between review dispatch and model choice, so independence of reviewer from author can only be enforced at the resolver itself.
