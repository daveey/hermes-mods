# PR-aware Kanban CI waits

Patches: 3786, 3823.

## Behavior

A CI wait naming a pull request can refuse an impossible park or wake an already parked owner when the PR conflicts or its head never receives a workflow run. A PR-specific fault does not create a shared runner-offline blocker.

## How it works

`progress` preserves the existing actions-run route, otherwise parses a repository/PR ref. Explicit `@` head SHAs take precedence. Without one, `_only_sha` uses a single distinct full SHA in the ref, then a single distinct hint SHA; multiple distinct candidates mean no pin, so the current head is examined instead of guessing an old/base SHA.

`pr_progress` reads the PR and head runs. Closed/merged or genuinely moved heads return `ok` for the card's own wake probe to resolve. Dirty/false mergeability returns `not_starting`; no run becomes unstartable after the later of park time and PR update has aged through the queued limit. Up to three queued head runs use the existing runner-progress checks. Null mergeability while settling is quiet/unknown. The sweep rechecks the latest park transactionally before waking, comments once and synthesizes the ended run for the PR-specific case.

## Code

- `hermes_cli/kanban_ci_progress.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/hermes_cli/test_kanban_ci_progress_pr.py` (patches 3786, 3823).

- **3786:** Parser, park and sweep cases pin conflict refusal, one wake/comment without shared blocker, fresh-head grace, no-run expiry, runner-offline routing for queued runs, quiet null mergeability, moved/closed heads and fail-open API errors.
- **3823:** Ambiguous distinct SHA hints still wake on current-head conflict; one old SHA treats a moved head as ok; parser rows distinguish repeated identical SHAs from multiple distinct candidates and explicit/ref precedence.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

GitHub failures fail open with warnings and later retry; they are not evidence of successful CI. Fresh heads with no runs are allowed to park. Ambiguous SHA hints intentionally inspect the current PR head. A queued-run pool failure still follows the pre-existing shared-blocker path.

## Plugin?

Not as a standalone plugin: park validation, sweep budgeting, transactional wake and run settlement are wired into core Kanban state.
