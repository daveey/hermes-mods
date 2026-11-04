# Desktop poll cost: revalidated annotations and cached status reads

Patches: **1722** (`b896f8ad68`, `[daveey] Desktop poll cost: revalidate kanban annotation on unchanged inputs, cache gateway platforms/manifest names/status session rows on file signatures`); **1724** (`b8de24c3d6`, `[daveey] Desktop poll cost: move the annotation revalidation and origin-work snapshot into topical siblings`); **1743** (`a4be890faf`, `[daveey] Bound cold annotation owners and followers to one refresh budget (#699)`); **1751** (`354d1ced6c`, `[daveey] test poll cache guarantees and invalidate same-stat board rows`); **1752** (`89bca82453`, `[daveey] keep board memo revalidation in topical sibling`); **1753** (`56ef77262b`, `[daveey] baseline cold-start bounded worker in poll cache guarantee tests`); **1756** (`33e3d73433`, `[daveey] refactor(kanban): move the recent-done board read to a sibling module (file cap)`); **1798** (`5012a147c5`, `fix(kanban): an uncontended cold annotation read past its budget serves the disk copy, not all-unknown [daveey]`); **1799** (`0436069dde`, `test(kanban): a replaced reader can't reuse a same-stat board memo row [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Past its TTL, the Kanban annotation is re-served inline when its fingerprint is unchanged, bounded by max ages for time-derived states; gateway platforms, bundled manifest names and status session rows are cached on file signatures (1722, moved into topical sibling modules by 1724/1752/1756).
- Cold annotation owners and followers share one refresh budget (1743).
- Same-stat board memo rows are invalidated when the reader is replaced; `board_memo_key` pins the reader identity (1751/1753/1799).
- An uncontended cold read past its budget serves the on-disk copy instead of marking every row `kanban_unknown` (1798).

## How it works

`kanban_session_activity` plus siblings `_revalidate`, `_origin_snapshot`, `_recent_done`; `kanban_annotation_refresh.refresh_bounded` takes the disk fallback on both paths.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1722: David-MBP profile (t_01a199a0a5d38255 / t_01a19d0bcab45558): the kanban annotation refreshed every ~3 s because the counts TTL expired while no board content moved (3 of 60 polls saw a revision change), and every /api/status poll reloaded the gateway config (re-parsing ~27 bundled manifests) and re-read state.db for the active-session count.
- 1724: kanban_session_activity stays under its line cap: the unchanged-input revalidation lives in kanban_session_activity_revalidate, the origin-work snapshot in kanban_session_activity_origin_snapshot (re-exported as _remote_snapshot, so its test seams stay). gateway.py nets zero lines.
- 1743: (subject only)
- 1751: (subject only)
- 1752: (subject only)
- 1753: (subject only)
- 1756: Offsets the cached_ro budget fix's growth: kanban_session_activity.py stays under its 2000-line target. _read_recent_done remains the patch seam.
- 1798: A fresh process's only poll (session-idle-check's collector, a restarted backend before polls overlap) passed no fallback to refresh_bounded, so on an overloaded host (load 24) one read slower than _REFRESH_WAIT_S marked every session row kanban_unknown even with a recent copy on disk. The contended path already seeded from that copy. t_01a19eee6379cea9
- 1799: PRAGMA data_version is connection-local, so a reader opened after a same-tick commit can report the value the evicted reader saw before it. Pin the reader identity in board_memo_key: evict the cached reader between two polls of a same-stat board and assert the second poll shows the write (kills mutation fix-F3, t_01a19ec10011d888).

## Code

`gateway/config.py`, `hermes_cli/gateway.py`, `hermes_cli/kanban_activity_revision.py`, `hermes_cli/kanban_annotation_refresh.py`, `hermes_cli/kanban_session_activity.py`, `hermes_cli/kanban_session_activity_attention.py`, `hermes_cli/kanban_session_activity_origin_snapshot.py`, `hermes_cli/kanban_session_activity_recent_done.py`, `hermes_cli/kanban_session_activity_revalidate.py`, `hermes_cli/web_routers/status.py`, `hermes_cli/web_server_gateway.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `tests/gateway/test_bundled_manifest_scan_cache.py`: `test_edited_manifest_is_read_again`, `test_repeated_scans_parse_no_manifest`, `test_unchanged_manifest_is_parsed_once`
- `tests/hermes_cli/test_gateway_platforms_poll_cache.py`: `test_a_config_write_shows_on_the_next_poll`, `test_a_failed_load_is_not_cached`, `test_a_home_switch_or_the_max_age_reloads`, `test_a_legacy_gateway_json_write_shows_on_the_next_poll`, `test_a_managed_layer_write_shows_on_the_next_request`, `test_a_same_tick_same_size_rewrite_is_not_hidden`, `test_an_env_write_or_dotenv_edit_shows_on_the_next_poll`, `test_identical_polls_load_the_config_once`, `test_request_home_switch_with_identical_environ_and_missing_files`
- `tests/hermes_cli/test_kanban_activity_revision.py`: `test_an_unchanged_schema_checks_trigger_coverage_once`
- `tests/hermes_cli/test_kanban_annotation_cold_bounded.py`: `test_cold_polls_finish_while_refresh_is_descheduled`
- `tests/hermes_cli/test_kanban_annotation_disk.py`: `test_uncontended_cold_read_past_budget_serves_the_copy`, `test_uncontended_cold_read_past_budget_without_a_copy_is_unknown`
- `tests/hermes_cli/test_kanban_annotation_refresh_bounded.py`: `test_fast_cold_scope_returns_counts_with_caller_context`
- `tests/hermes_cli/test_kanban_annotation_unchanged_inputs.py`: `test_a_board_without_the_revision_counter_keeps_the_ttl_bound`, `test_a_board_write_shows_on_the_next_poll`, `test_a_settled_stat_keyed_board_revalidates_until_it_is_written`, `test_attention_checks_rerun_only_when_board_content_moved`, `test_time_derived_states_are_re_read_after_the_max_age`, `test_unchanged_polls_past_the_ttl_reuse_one_read_without_a_refresh_thread`
- `tests/hermes_cli/test_kanban_poll_cache_guarantees.py`: `test_expired_attention_with_moved_key_requires_bounded_refresh`, `test_inline_poll_reads_each_board_revision_once`, `test_other_scope_insert_retains_expired_unchanged_attention`, `test_recent_stat_attention_rechecks_verdict_after_ttl`, `test_recent_stat_board_same_tick_write_is_read_after_ttl`, `test_replaced_reader_cannot_reuse_same_stat_board_row`
- `tests/hermes_cli/test_kanban_session_activity.py`
- `tests/hermes_cli/test_status_active_sessions_cache.py`: `test_a_racy_store_is_never_cached`, `test_a_session_write_shows_on_the_next_poll`, `test_the_activity_window_moves_with_the_clock`, `test_unchanged_store_is_read_once`

Untested by their own patch (refactors, constants or CI routing): 1724, 1752, 1756.

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- `PRAGMA data_version` is connection-local, so revision equality alone cannot prove a replaced reader saw no write.

## Plugin?

No: these change core modules that plugins cannot hook.
