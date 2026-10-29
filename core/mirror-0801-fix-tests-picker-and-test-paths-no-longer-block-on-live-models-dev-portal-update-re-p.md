# fix(tests): picker and test paths no longer block on live models.dev/portal; update re-prepares the retirement fence [daveey]

Patch 0801, source `945b93122693b37e42556f1a9395c75b94db5f57`.

## Behavior

fix(tests): picker and test paths no longer block on live models.dev/portal; update re-prepares the retirement fence [daveey]. This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _await_background_reviews(pids: list[int]) -> None:`
- `def get_label(provider_id: str) -> str:`
- `def client(monkeypatch, _isolate_hermes_home):`
- `same way the hermes model CLI picker does.`
- `def test_apply_pricing_omits_sale_when_original_not_cheaper(monkeypatch):`
- `def test_model_options_cold_pricing_fetch_runs_off_the_request_path(monkeypatch)`
- `_BACKEND = textwrap.dedent(`
- `def test_control_backend_without_a_reachable_fence_is_stopped_as_today(tmp_path,`
- `def test_home_keyed_caches_serve_each_profile_its_own_config(tmp_path, monkeypat`
- `from tui_gateway import server`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> CI run 40724540437 (main d55299af) Python shards failed or flaked on five
> network- or race-dependent tests. Each was reproduced deterministically.
>
> - providers.is_aggregator() fetched models.dev in the foreground on a cold
>   cache, although the flag only comes from HERMES_OVERLAYS. The picker's
>   read path (_strip_aggregator_overlaps) blocked 7-10s in CI. It is now
>   cache-only. The test asserts on the request thread with no network lookup
>   instead of a 2s wall clock, and it fails red on the base.
> - test_multiplex_tool_cache_scope: A's native-vision verdict came from the
>   live models.dev catalog; it is pinned to a fixture catalog.
> - test_config_rmw_lock: switch_model's catalog lookups fetched models.dev
>   inside the writer's lock span (a 31s fetch reproduces 'first writer never
>   reached save_config'). The tests now run with models.dev offline.
> - test_connector_accounts_rpc: an account connect minted a guest identity
>   LIVE against portal.nousresearch.com ('no reply' under a slow portal).
>   ensure_guest_identity is stubbed, as in test_connector_account_surface.
> - dashboard_procs._await_background_reviews: a review ending between a
>   refused prepare and the idle probe stopped the backend without committing
>   the fence. One re-prepare now commits it, with scripted tests for the
>   retry and its one-retry bound. The stand-in backend's event log is locked
>   and written atomically.

## Code

- `hermes_cli/dashboard_procs.py`
- `hermes_cli/providers.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_cli/test_config_rmw_lock.py`
- `tests/hermes_cli/test_inventory_pricing.py`
- `tests/hermes_cli/test_update_stale_dashboard_review.py`
- `tests/tools/test_multiplex_tool_cache_scope.py`
- `tests/tui_gateway/test_connector_accounts_rpc.py`

New or changed test definitions present in the source diff:

- `test_review_ending_between_prepare_and_idle_probe_still_commits_the_fence`
- `test_a_backend_that_stays_unpreparable_after_its_review_is_stopped_as_before`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
