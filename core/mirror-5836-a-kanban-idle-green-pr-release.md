# Kanban: release idle green PR owners and bound publication holds (mirror 5532, 5533)

## Behavior
- The `active_pr` duplicate-publication guard now discharges a bound open PR whose required checks finished green: an idle ready publisher is admitted when every unfinished holding PR is green (including local-only multi-publication cards). Exact-head required CI acceptance is reused; missing evidence fails closed; open-run and wake-probe custody are preserved; custody/publication identity is revalidated before admission. Per the module docstring and docs, admission is only to finish the existing PR: it is not merge approval and does not bypass review or landing, and repeated crash recovery stays subject to the ordinary run/spend breakers.
- Observations are collected in a two-worker background pool with no backlog; guard ticks never wait. The cache key is (database list, task id, contract, assignee, newest comment id, newest run id, PR URL, head); observations taking over 120 s are discarded, entries refresh after 300 s, cache bounded at 256.
- Remaining `active_pr` holds are capped at 2100 s (35 min) using the existing supervisor escalation; shorter operator caps are allowed but never exceed 35 minutes. The 30-minute notice is kept.
- Completed futures are popped before their results are read; any ordinary `Exception` from the observer counts as unknown CI (not green) and its text is not retained. The head re-check inside the pool thread (5533) calls the GitHub API directly instead of `prs.pr_head`, so a pool thread never mutates the dispatch thread's PR-state dictionaries.

## How it works
`kanban_pr_idle_green.py`, `kanban_pr_idle_checks.py`, `kanban_block_routing.py`, `kanban_db_dispatch.py`, `kanban_db_dispatch_pr_state.py`; docs in `website/docs/user-guide/features/kanban.md`.

## Code
All under `hermes_cli/` as listed.

## Tests
Source: `test_kanban_active_pr_idle_green.py`, `_async.py`, `_custody.py`, `_cache.py`, `_thread.py` (by test names: an idle bound green owner is released on the next tick, even through real dispatch; missing or non-green exact head stays held; an active run or live wake watch stays held without a CI lookup; local-only publication resumes after green; blocked network does not block the guard; only two jobs and no backlog; late observation discarded; new publication during collection does not reuse green; multiple publications all need green; failed observation stays held and failed futures are removed; cache hard size bound; refresh after five minutes; custody/head revalidation races; unknown or extra holding PR not discharged; missing head, invalid or uncovered contract, and disappearing task identity not admitted; cap at most 35 minutes and an operator can lower it; real collect pool uses an isolated final-head read). Not executed in this mirror pass.

## Applying
Apply in order.

## Gotchas
Observation age includes reaping delay, so old green data is deliberately rejected.

## plugin?
No.
