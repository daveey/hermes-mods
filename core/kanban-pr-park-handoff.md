# PR guard handoff after an orderly park

Patch: **0280**, source `1a2ba03afccbee694b5dcb1adee73e85bb42be3d`.

## Behavior

An orderly continuation or scheduled wait strictly after a worker's PR comment releases the `active_pr` respawn hold when that card wakes. A crash is not a handoff, a same-second tie stays guarded, and a newer PR comment re-arms the hold. This changes dispatcher guard decisions, not PR acceptance or merge authority.

The dispatcher PR-state read uses the configured `kanban.pr_acceptance.gh_command_prefix` and platform-resolved GitHub CLI. This supports service processes with a minimal PATH and no ambient login. Setup errors fall back to bare `gh`; failed lookups remain fail-closed.

## How it works

`active_pr_reason` includes `continued` and `scheduled` among candidate handoff events, filtered through the existing handoff predicate and strict timestamp comparison. `_gh_argv` builds an argv list using the PR acceptance prefix resolver and `resolve_gh`. Configuration errors are logged without exception text to avoid credential disclosure.

## Code

- `hermes_cli/kanban_db_dispatch_pr_state.py`: handoff eligibility and broker-aware PR lookup.
- `tests/hermes_cli/test_kanban_review_lifecycle.py`: continuation/schedule handoffs, crash control and newer-comment rearming.
- `tests/hermes_cli/test_kanban_park_invariants.py`: resolved executable and broker argv, setup fallback and failed-lookup behavior.

## Tests

The applied public series passed **68 tests**, with **0 failures** and **1 macOS-only skip**, on Linux using:

```sh
bash scripts/run_tests.sh tests/hermes_cli/test_kanban_park_invariants.py tests/hermes_cli/test_kanban_review_lifecycle.py -j1
```

Tests ran in an isolated standalone clone of the applied series. Broker subprocess calls were mocked; live credentials, actual dispatcher deployment and Desktop rendering were not tested by this mirror run.

The full ordered **277-patch** `git am -3` succeeded at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`, with upstream `749cb638ff43f9ce5333c186a1e4f67d473aa4f2` and fork `1a2ba03afccbee694b5dcb1adee73e85bb42be3d`. Prior privacy scrubs and patch 0242's merge adaptation were retained. This patch additionally generalizes private incident details in prose without changing behavior.

## Applying

Apply the complete ordered series from its merge-base. This depends on the earlier PR guard and broker transport work, including patch 0278.

## Gotchas

An orderly park must follow the relevant PR comment; it does not globally disable duplicate-work protection. Missing or broken GitHub access can still hold cards absent a qualifying handoff. This patch is distinct from continuation-budget exhaustion routing and does not implement that separate guard.

## Plugin relationship

This is core dispatcher behavior, not a disk plugin. Publishing the mirror does not restart or deploy any runtime.
