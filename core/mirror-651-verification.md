# Public mirror verification through 0651

Source snapshot: `90a63800fbcd1dd41962e75954c493c4aa9b9cb8` (fork main). Appended public range 0631–0651 follows `7f117fa465`; the sidebar patch previously published as 0631 is now 0647. No other historical public patch numbers changed. The existing curated-series adaptations/intentional duplicate handling remain as described in README.

## Inventory and privacy

The entire `mirror-upstream/main..90a63800fb` non-merge log was regenerated into a scratch directory and compared with the published inventory. Its raw log has 657 commits; the curated public series has 651 numbered patches. Raw ordinal numbers are not the published numbers (see README's earlier reconciliation notes). All 21 commits after `7f117fa465` appear once, in fork order, as 0631–0651.

New patch headers use `daveey <daveey@users.noreply.github.com>`. Standard path/application substitutions follow the repository's mirror procedure. Patch 0646 retains the already-public 0565 substitutions for example AWS profile markers (`example-org`, `example-sso`, `--profile example`) and the generic application-skill prefix. These are context substitutions, not new placement policy.

## Patch application

Executed `git am -3` for 0631–0650 in a detached throwaway worktree at fork commit `7f117fa465`, then applied 0651. The private fork base needed its placement marker context normalized to the existing public 0565 substitutions before 0646 could apply. The corresponding old test's profile string was normalized too before rerunning placement tests. No regression assertion or acceptance criterion was weakened.

The complete curated series was repaired and re-run on 2026-10-28:

```sh
git worktree add --detach <scratch> 4957bbd0a0
git -C <scratch> am -3 --empty=keep <mods>/core/patches/*.patch
```

That command, against the then-published 664 patches (0001–0664), exited 0. Two patches 3-way-merged to "already applied" (a duplicate exact-owner admission commit, and the i18n `_keys.desktop.json` regenerate) and did not add a commit; 0610 was kept as an empty commit. Patches 0665 and 0666, published while that apply ran, then applied cleanly on the result. No manual conflict resolution.

Problem: the same command previously applied 0001–0509 and stopped at 0510 with a content conflict in `apps/desktop/src/store/session-dot-state.ts` (log `out-1793171518-3540167-e3f0.log`; reproduced before the edit).
Why 1: 0510's `withRowActivity` row type was cut against the fork's merge-resolved field order. The linear series still has patch 0364's order (`kanban_unseen | offline_cached | kanban_observed_at | kanban_remote_stale | kanban_unknown`). Evidence: the conflict hunk, and each order matching once in the patch set.
Why 2: the published series is not a linear `format-patch` of `upstream/main..origin/main`. From 0343 it has a different order, and merge resolutions such as `165df7ddf9` are not patches, so fork context never appears in the series tree.
Why 3: the next fork-context patches then conflicted the same way. 0543 assumed 0557's `_replica_decoded` cache, which the series applies later. 0545 re-added a stalled block 0555 had already introduced. 0604/0618 assumed a lockguard scan the series had already replaced with `replica_cards`. 0610 rewrote `198.51.100.7` to `192.0.2.10`, which 0592 already did. Evidence: each `git am --continue` stopped at the next of these.
Root (happened): patches are exported from the fork's merge-resolved tree and appended in fork order, while the public series is a curated linearization. Context that exists only because of a merge, or because a later fork commit is numbered earlier, does not apply.
Root (not caught): full-series apply was written down as a known gap (this file, README, mirror-reconciliation.md). `core/check_coverage.py` checks doc rows, not applyability, so a later append could publish on top of a series that does not apply.
Recurrence: the same stop was already recorded (the pre-existing series stopped at its 0467, the same patch). Recording the gap did not adapt the patch, so the next full-series run hit it again.
Countermeasures: the seven patches above were adapted to series context and their numbers kept. Proof is the exit-0 apply. A standing "re-run the full series before every append" gate is not in this change; the apply takes several minutes and would collide with an in-flight append.

Equivalence limit: 0555's stripe-trace log still omits `kanban_stalled`. Folding that field into the indexed 0555 patch makes `git apply --check` fail, because the patch no longer matches its recorded blobs. The stripe itself counts stalled work (0545); the omission is the diagnostic log line only.

Repair tests, on the applied tree, with `HERMES_PYTHON` pointing at the live checkout's `.venv` and desktop `node_modules` symlinked from an existing worktree: `tests/hermes_cli/test_kanban_session_activity.py`, `test_kanban_remote_inventory_cache.py` and `test_kanban_replica_decode_memo.py` 64 passed; `test_kanban_chat_links.py` and `test_kanban_chat_links_parity.py` 32 passed; `apps/desktop` vitest `src/store/session-dot-state.test.ts` 68 passed. Untested here: a full desktop `tsc`/`eslint` run, and every other test file in the applied tree.

## Executed regression tests

The canonical runner `bash scripts/run_tests.sh <files> -j 3 --file-timeout 180` used an existing PM-built test interpreter via `HERMES_PYTHON`. Fresh worktree auto-bootstrap failed with `packaged PM runtime is missing or invalid`; the explicit test-interpreter path succeeded. The runner still supplied its normal clean environment and per-file process isolation.

| File under tests/ | Result |
|---|---|
| hermes_cli/test_kanban_db_recover.py | 28 passed |
| hermes_cli/test_kanban_sync_reconcile.py | 6 passed |
| hermes_cli/test_kanban_placement.py | 88 passed after matching the existing public profile-string scrub (initially 87 passed, 1 private-base string mismatch) |
| tools/test_kanban_creator_archive.py | 4 passed |
| hermes_cli/test_kanban_awaiting.py | 59 passed |
| gateway/test_kanban_dispatcher_health.py | 15 passed |
| hermes_cli/test_kanban_diagnostics.py | 9 passed |
| hermes_cli/test_kanban_class_unroutable.py | 7 passed |
| hermes_cli/test_kanban_class_routing.py | 10 passed |
| hermes_cli/test_kanban_false_signals.py | 7 passed |
| pm/test_downloader_integrity.py | 13 passed |
| test_conftest_kanban_env_scrub.py | 2 passed |
| tui_gateway/test_profiles_list_canonical_session.py | 30 passed |
| tui_gateway/test_profiles_list_worker_session.py | 3 passed |
| test_hermetic_env_systemd_markers.py | 2 passed |
| tools/test_git_consumer_environment.py | 1 passed, **4 failed**, still failed on runner retry |

The four git-consumer failures are the unsupported-target/acquisition × checkpoint/diff cases: consumer calls succeed, but the injected failure callback was never called (`assert calls` is false). Patch 0638 is mirrored faithfully; this host result is not a claim that its fallback-injection coverage is green. A separate core repair follow-up was filed; the root cause remains undetermined pending tracing the PM lookup seam. Follow-up: [0662 git-consumer boundary repair](git-consumer-fallback-tests.md) reproduces this four-failure mechanism with a missing packaged runtime and records the fix and green runs; the exact original host state remains unproven.

Desktop: installed lockfile dependencies with `npm ci --ignore-scripts --no-audit --no-fund` in the disposable worktree, then ran `npx vitest run src/store/session-watchdog.test.ts`: **25 passed**. No live Electron multi-window probe, Windows recovery probe or full repository test run is claimed.

## Late contiguous addendum: 0652–0653

The final source refresh added `a934e39df2` (0652, safe recovery pid probe and wrapper test paths) and `9c943cbd2f` (0653, concrete pin-provider binding). Both public patches applied with `git am -3` in a new detached worktree at `90a63800fb`. Canonical runner results: recovery **28 passed** again, pin-provider **19 passed**, and targeted backup/doctor wrapper cases **2 passed**. That worktree was removed too. This extends the published inventory to **653 contiguous patches**, representing all **23** fork commits after `7f117fa465` through `9c943cbd2f`. The original 657-commit regeneration was followed by this two-commit append.

## Concurrent mirror reconciliation: 0654

Another publisher appended `599752f5c9` as 0652 while 0652–0653 were being verified here. Its patch and docs were retained, merged and renumbered to **0654**, following the actual fork order after `9c943cbd2f`; all its feature references were updated. Applied 0654 with `git am -3` at `9c943cbd2f` and ran `tests/hermes_cli/test_kanban_origin_projection_contention.py`: **5 passed**. The throwaway worktree was removed. The final series is contiguous through **0654** (`599752f5c9`), with no duplicate numbers or appended source commits.

## Final source refresh: 0655

`bcf47021db` was added during the publish verification and is mirrored as **0655**. Its public patch applied with `git am -3` at `599752f5c9`; the canonical borrowed-home launch suite passed **7 tests**. The worktree was removed. The published series is contiguous through this source snapshot, with every commit after `7f117fa465` represented once.

## Recovery reporting precision

The recovery implementation writes a JSON report file and emits markdown-style summary/card content. It does not create a separate `.md` file alongside every JSON report. The feature doc reflects that distinction rather than implying an artifact the implementation does not write.
