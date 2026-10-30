# Boot revision, plugin fingerprints and backend idle work

Patches: 1160 1161 1180 1181 1185 1186 1191

## Behavior

- Bootstrap records the checkout revision after interrupted-update repair, before the main module's eager imports. The later code-skew guard prefers that revision rather than silently adopting an update that landed during startup.
- An `ImportError` under checkout skew emits one `code-skew import mix` warning per boot/disk fingerprint pair, naming both revisions, on tool-registry dispatch and execute-code session-kernel error paths.
- The updater includes `hermes_cli/git_fingerprint.py` in its update-critical file inventory.
- Plugin discovery fingerprints ignore directory-mtime changes caused by bytecode-cache creation, while still detecting added plugin source files and installed-distribution entries.
- A cold backend poll can serve the previous process's recent Kanban annotation while its first annotation read is already running; the copy retains its original observation time.
- Remote task observations reuse decoded summaries for unchanged row sequences. Non-repository working directories keep a negative Git-probe result for 300 seconds unless their own mtime changes first.
- Desktop transcript prefetch remembers empty and missing-session results, avoiding another fetch until session activity or message count changes and the minimum refetch interval permits it.

## How it works

`hermes_cli/git_fingerprint.py` reads Git metadata without spawning Git and records the first fingerprint per resolved checkout root. `hermes_cli.main` retains aliases for the moved readers. Patch 1161 narrows optional import catches to `ImportError`, removes the note helper's blanket catch, and explicitly exempts bootstrap's best-effort launch guard from the health check.

Plugin-root directory entries retain device/inode identity, not mtime; site-directory fingerprints enumerate distribution metadata suffixes rather than stat the site directory itself.

Annotations are atomically saved under `<HERMES_HOME>/cache/kanban-annotation/<scope-hash>.json`. A same-scope copy at most 300 seconds old seeds a contended cold read; publishing the process's own read retires it. Unchanged publications are throttled until half the maximum age.

Remote-observation memo keys include replica, owner and task key; changed sequences fetch and decode only their row, retaining the fields needed for notice persistence. Transcript prefetch keeps an in-memory unstored-result memo keyed by connection/profile/session, prunes untargeted sessions, and clears an entry when a cache write succeeds.

## Code

- `hermes_bootstrap.py`, `hermes_cli/git_fingerprint.py`, `hermes_cli/main.py`, `hermes_cli/update_cmd.py`, `gateway/code_skew.py`, `tools/registry.py`, `tools/code_kernel.py`.
- `hermes_cli/plugins_snapshot.py`, `hermes_cli/kanban_annotation_disk.py`, `hermes_cli/kanban_activity_identity.py`, `hermes_cli/kanban_session_activity.py`.
- `tui_gateway/git_probe.py`, `tui_gateway/kanban_remote_observations.py`, `tui_gateway/kanban_remote_notices.py`, `apps/desktop/electron/transcript-prefetch.ts`.

## Tests

These are source test coverage, not a claim of execution for this documentation change.

- `tests/gateway/test_boot_pinned_imports.py`: `test_an_update_between_process_start_and_gateway_bootstrap_still_pins_the_boot_revision` and `test_hermes_bootstrap_records_the_boot_revision_before_hermes_cli_main_loads` cover startup ordering and pinned lazy imports.
- `tests/gateway/test_code_skew_import_mix.py`: `test_registry_dispatch_path_names_the_mix`, `test_execute_code_session_kernel_path_names_the_mix`, and helper tests cover both error paths, warning deduplication, unchanged revisions and bootstrap fallback. Patch 1161 adds no tests.
- Patch 1180 adds no tests; its message names the existing inventory check `test_the_critical_inventory_covers_every_module_the_entry_paths_import_first` in `tests/hermes_cli/test_update_post_pull_syntax_guard.py`.
- `tests/hermes_cli/test_kanban_capabilities_plugin_snapshot.py::test_fingerprint_ignores_bytecode_cache_creation_but_sees_plugin_changes` covers bytecode/directory-mtime stability, source addition and distribution addition.
- `tests/hermes_cli/test_kanban_annotation_disk.py` covers round-trip types, unusable copies, test isolation, write deduplication, contended cold polls, retained observation dates, replacement by a fresh read and waiting without a copy.
- `tests/tui_gateway/test_git_probe_negative_cache.py` covers retention past 30 seconds, cwd-mtime invalidation with a mocked Git response, and TTL expiry. `tests/tui_gateway/test_kanban_remote_observations.py::test_changed_snapshot_decodes_only_the_rows_whose_seq_moved` checks one decode for one changed row.
- `apps/desktop/electron/transcript-prefetch.test.ts`: `does not refetch an empty or missing session until its activity or message_count moves` checks empty/404 suppression and an activity-triggered retry. Other error statuses, unstorable nonempty pages, message-count-only retry and live Desktop restart behavior are untested by the added test.

## Applying

Apply the complete numbered series in `patches/` at the fork/upstream merge-base with `git am -3 --empty=keep`, in numeric order; see [README.md](README.md). These entries are not independent patches.

## Gotchas

The annotation copy is only a cold-start fallback, not fresh evidence; absent, corrupt, expired or wrong-scope copies do not avoid waiting. It is disabled under `HERMES_TEST_ISOLATION` unless the test-only `FORCE` flag is set. A parent directory becoming a repository may wait for negative-cache expiry. Transcript unstored-result memos do not survive process restart. Live restart/load performance and the shared Git-root probe entry path are untested by the added tests; no measured speedup is asserted here.

## Plugin?

No, core change: bootstrap ordering, updater inventory, discovery fingerprints, backend caches and Electron prefetch are core-owned paths.
