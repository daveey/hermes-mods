# feat(browser-use): agent tab ledger — record, cap and close tabs agents open in the shared Chrome [daveey]

Patch 0857, source `410bb3343364ccb30742a7d829c1c5e3d722a44b`.

## Behavior

feat(browser-use): agent tab ledger — record, cap and close tabs agents open in the shared Chrome [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _no_host_browser_use_cli():`
- `"""Managed Chrome entry paths use owner leases, never ambient/shared daemons."""`
- `def test_managed_setup_failure_and_missing_owner_never_dispatch(managed, monkeyp`
- `+"""Agent tab ledger: tabs agents open in the user's shared Chrome are recorded, capped and closed.`
- `def cleanup_browser(task_id: Optional[str] = None) -> None:`
- `_hermes_ensure_own_tab()`
- `def _find_cli() -> Optional[list[str]]:`
- `def record_daemon_owner(env: dict, name: str) -> None:`
- `def reap_orphaned_harness_daemons(env: Optional[dict] = None, *, drop_own: bool`
- `def _maybe_reap_harness_daemons(env: dict) -> None:`
- `def browser_exec(code: str, session: str = "", timeout_s: int = _DEFAULT_TIMEOUT`
- `_HELPERS_DIGEST = (`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> Agents driving the user's real Chrome through browser_exec open tabs (new_tab, the
> own-tab preamble's Target.createTarget) and never close them; nothing recorded which
> tabs were an agent's, so nobody could close them safely (59 tabs, load 150+).
>
> - _TAB_LEDGER_PREAMBLE, prepended on shared browsers for every session (before the
>   own-tab pin), wraps cdp/new_tab/close_tab in the exec namespace: created targets
>   go to <harness runtime>/hermes-agent-tabs/<owner>.json, closeTarget drops them.
>   A blank tab new_tab() reuses is not recorded.
> - Caps before every create: browser.agent_tab_cap_per_owner (3) closes the owner's
>   oldest; browser.agent_tab_cap (8) first closes tabs of owners idle > 600 s, then
>   raises telling the agent to close_tab.
> - close_agent_tabs(owner) closes only ledger ids through the recording daemon
>   (BH_REQUIRE_EXISTING_DAEMON: never spawns one), called from cleanup_browser;
>   the harness reaper sweeps owners idle past browser.agent_tab_idle_s (1800).
> - browser_exec refreshes the owner's last_used; tool description asks agents to
>   close their tabs.
> - tests/tools conftest pins BH_RUNTIME_DIR to a temp dir so tests never touch the
>   host's harness runtime (ledger, owner stamps).
>
> Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>

## Code

- `tools/browser_tool_lifecycle.py`
- `tools/browser_use_cli.py`

## Tests

Source test files changed by this commit:

- `tests/tools/conftest.py`
- `tests/tools/test_browser_agent_lifecycle.py`
- `tests/tools/test_browser_agent_tabs.py`

New or changed test definitions present in the source diff:

- `test_preamble_records_created_tab_and_drops_it_on_close`
- `test_reused_blank_tab_is_not_recorded`
- `test_per_owner_cap_closes_owners_oldest_tab_first`
- `test_global_cap_closes_idle_owners_tabs_first`
- `test_global_cap_raises_when_only_active_owners_hold_tabs`
- `test_cleanup_browser_closes_exactly_that_owners_tabs`
- `test_reaper_closes_tabs_of_idle_owners_only`
- `test_ids_not_in_ledger_are_never_closed`
- `test_corrupt_ledger_file_is_treated_as_empty`
- `test_browser_exec_ships_ledger_preamble_on_shared_browser`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
