# CI concurrency group, tool-description catalog wording and health-cap refactors

Patches: 0983, 1054, 1127, 1052.

## Behavior

- `catalog-tests.yml` uses the fork's combined per-run concurrency expression on main, like `tests.yml` and `lint.yml` (983).
- Fork tool descriptions open with a whole first sentence of at most 80 characters for upstream's catalog; the prompt-surface fixtures are regenerated (1054).
- Behaviour-preserving extractions bring files back under the ratchet size caps after the merge (1127).
- The parallel test runner counts a file's setup errors once, and its summary says "N errors" (1052).

## How it works

983 changes only the workflow `concurrency` group. 1054 rewrites descriptions in `tools/session_move_tool.py` and `tools/kanban_tools_schemas.py` and bumps `tool_tokens` in `tests/fixtures/prompt_surface/*.json` by one. 1127 moves code: sidebar nav rows to `nav-items.tsx`, search merge to `search-results.ts`, filter predicate to `session-filters.ts`, repo discovery policy to `projects-repo-discovery.ts`, desktop PE helpers to `main_desktop_pe.py`, session workspace SQL to `hermes_state_sessions_workspace.py`, plus helper extractions in dashboard auth middleware, fleet verify and `browser_use_cli`. 1052 drops a duplicate error counter in `scripts/run_tests_parallel.py`.

## Code

- `.github/workflows/catalog-tests.yml` (983)
- `tools/session_move_tool.py`, `tools/kanban_tools_schemas.py`, `tests/fixtures/prompt_surface/` (1054)
- `apps/desktop/src/app/chat/sidebar/`, `store/projects*.ts`, `hermes_cli/main_desktop_pe.py`, `hermes_state_sessions_workspace.py` (1127)
- `scripts/run_tests_parallel.py` (1052)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. 983's message cites `tests/ci/test_ci_concurrency_groups.py`. 1052 edits `tests/scripts/test_run_tests_parallel.py` (assertion now expects "2 errors"). 1054 depends on the regenerated prompt-surface fixtures. 1127 adds no tests and is claimed behaviour-preserving by its message only.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

Regenerate prompt-surface fixtures if tool descriptions change again. A refactor-only patch like 1127 will conflict with any other change to the moved code. Any "behaviour-preserving" claim is the author's, not checked here.

## Could this be a plugin instead?

No: repository CI and file layout.
