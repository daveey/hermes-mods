# PR acceptance: reported CI when no checks are required

Mirror snapshot 4352; ordinals 4188, 4189, 4190, 4191, 4192, 4216, 4230.

Entries:

- **4188**: Require reported CI when PRs have no required checks
- **4189**: Preserve distinct workflow suites in all-check merge evidence
- **4190**: Keep same-name sibling checks in merged fallback evidence
- **4191**: Ignore idle app suites and accept skipped/neutral runs in no-required fallback
- **4192**: Let a pre-merge rerun supersede its failed run in the no-required fallback
- **4216**: Close merged-path fail-opens in the no-required-check CI fallback
- **4230**: Raise ci-red main_health for failed empty post-merge suites

## Behavior

When a pull request's base branch has no required checks, PR acceptance previously had no basis to admit it. These entries add a bounded "absence waiver" that is granted only on complete, exact-head, all-green evidence, and tighten it through review follow-ups.

- Collect complete exact-head evidence, apply an all-check fallback, preserve the merge cutoff and any required contexts, refuse empty suites and malformed pages, keep CLOSED and landing dispositions unchanged (4188).
- Distinct workflow suites and same-name sibling jobs are kept separate in merged evidence; a later sibling cannot hide a red or pending run unless proven superseded (4189, 4190).
- Zero-run suites left queued by installed apps are idle (ignored, recorded in `receipt.idle_suites`) only when still queued, the app has no runs or non-empty suites on the commit, and older than a 15-minute grace; a queued empty GitHub Actions suite is never idle. Completed exact-head skipped and neutral runs count as success in the fallback (4191).
- A failed run re-run to success before merge supersedes the failed run, but only when it completed and another run in the same (name, app, suite) started strictly later; overlapping, tied or never-completed runs stay as evidence (4192). 4216 tightens this: supersession requires a proven rerun (same workflow run id, higher `run_attempt`, resolved through the Actions jobs API); a completed non-passing empty suite counts as failure on the merged path unless provably created after `mergedAt`; ties and unreadable times fail closed.
- A failed empty post-merge suite raises the `ci-red` main-health pointer through `flag_main_health()`, with `startup_failure` added to the red conclusions and `cancelled`/`stale`/`neutral`/`skipped` not red (4230).

## How it works

- `hermes_cli/kanban_pr_acceptance.py` and `kanban_pr_acceptance_store.py` orchestrate evidence; `kanban_pr_fallback.py` classifies the no-required fallback; `kanban_pr_merge_time.py` handles merged-state evidence and supersession; `kanban_pr_acceptance.collect_acceptance` calls `flag_main_health` after `fallback_outcomes`. The required-check path is unchanged.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_pr_acceptance.py`
- `hermes_cli/kanban_pr_acceptance_store.py`
- `hermes_cli/kanban_pr_fallback.py`
- `hermes_cli/kanban_pr_merge_time.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_pr_acceptance.py`
- `tests/hermes_cli/test_kanban_pr_fallback.py`
- `tests/hermes_cli/test_kanban_pr_fallback_merged.py`
- `tests/hermes_cli/test_kanban_pr_landing.py`
- `tests/hermes_cli/test_kanban_pr_merge_time.py`
- `tests/hermes_cli/test_kanban_pr_transport.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- Fail-closed by design: unreadable apps, ages or jobs keep the earlier run. Main health is diagnostic only; acceptance classification is unchanged by 4230.
- A repo with a mix of idle app suites and real workflows depends on the 15-minute grace.

## Plugin?

None.
