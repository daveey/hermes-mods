# fix(tui_gateway): finalize sessions in their own profile scope [daveey] (#182)

Patch 0930, source `b9459ef5d01620b981d8b48b3edd0a0b08be8a9d`.

## Behavior

fix(tui_gateway): finalize sessions in their own profile scope [daveey] (#182). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `def _iter_darwin_sidecar_holders(db_path) -> list[tuple[int, str]]:`
- `def iter_deleted_sqlite_sidecar_holders(db_path) -> list[tuple[int, str]]:`
- `def test_darwin_clean_verdict_is_dropped_once_a_sidecar_is_unlinked(tmp_path, fa`
- `+"""Session teardown must not rediscover plugins for a profile it already loaded.`
- `def _finalize_session(session: dict | None, end_reason: str = "tui_close") -> No`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * fix(tui_gateway): finalize a session in its own profile scope [daveey]
>
> A multiplexed reaper has an empty context. on_session_end already ran
> inside the session scope, but on_session_finalize ran after it closed,
> so the first teardown of a secondary profile discovered and fired the
> launch profile's plugins — a second full manifest scan. The per-home
> manager cache then makes the next finalize of that profile free.
>
> * fix(hermes_state): reuse a clean Linux deleted-WAL fd scan [daveey]
>
> Every state.db writer open walked every process fd. Darwin already
> caches a clean verdict for 30s, invalidated by directory mtime or
> sidecar identity. Linux now does the same. Positive verdicts are not
> cached, and a sidecar retired during the walk is not published as clean.
>
> * fix(tui resume): read unanswered-turn tail before handing the profile db to the hydration worker; deflakes deferred-resume db ownership test

## Code

- `hermes_state_dbfile.py`
- `tui_gateway/session_lifecycle.py`

## Tests

Source test files changed by this commit:

- `tests/hermes_state/test_deleted_wal_generation_guard.py`
- `tests/tui_gateway/test_finalize_plugin_discovery.py`

New or changed test definitions present in the source diff:

- `test_linux_clean_holder_scan_is_reused_until_a_sidecar_changes`
- `test_linux_positive_holder_scan_is_not_cached`
- `test_two_finalizations_discover_each_profile_at_most_once`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
