# Public mirror verification through 0651

Source snapshot: `90a63800fbcd1dd41962e75954c493c4aa9b9cb8` (fork main). Appended public range 0631–0651 follows `7f117fa465`; the sidebar patch previously published as 0631 is now 0647. No other historical public patch numbers changed. The existing curated-series adaptations/intentional duplicate handling remain as described in README.

## Inventory and privacy

The entire `mirror-upstream/main..90a63800fb` non-merge log was regenerated into a scratch directory and compared with the published inventory. Its raw log has 657 commits; the curated public series has 651 numbered patches. Raw ordinal numbers are not the published numbers (see README's earlier reconciliation notes). All 21 commits after `7f117fa465` appear once, in fork order, as 0631–0651.

New patch headers use `daveey <daveey@users.noreply.github.com>`. Standard path/application substitutions follow the repository's mirror procedure. Patch 0646 retains the already-public 0565 substitutions for example AWS profile markers (`example-org`, `example-sso`, `--profile example`) and the generic application-skill prefix. These are context substitutions, not new placement policy.

## Patch application

Executed `git am -3` for 0631–0650 in a detached throwaway worktree at fork commit `7f117fa465`, then applied 0651. The private fork base needed its placement marker context normalized to the existing public 0565 substitutions before 0646 could apply. The corresponding old test's profile string was normalized too before rerunning placement tests. No regression assertion or acceptance criterion was weakened.

The complete curated series was separately attempted using:

```sh
git worktree add --detach <scratch> 4957bbd0a0
git -C <scratch> am -3 --empty=keep <mods>/core/patches/*.patch
```

It applies through 0509, then conflicts at **0510** in `apps/desktop/src/store/session-dot-state.ts`. This reproduces the previously documented historical gap, before this appended range; it is not a verified end-to-end install from upstream. A dedicated repair follow-up was filed. Both throwaway worktrees were removed after verification.

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

The four git-consumer failures are the unsupported-target/acquisition × checkpoint/diff cases: consumer calls succeed, but the injected failure callback was never called (`assert calls` is false). Patch 0638 is mirrored faithfully; this host result is not a claim that its fallback-injection coverage is green. A separate core repair follow-up was filed; the root cause remains undetermined pending tracing the PM lookup seam.

Desktop: installed lockfile dependencies with `npm ci --ignore-scripts --no-audit --no-fund` in the disposable worktree, then ran `npx vitest run src/store/session-watchdog.test.ts`: **25 passed**. No live Electron multi-window probe, Windows recovery probe or full repository test run is claimed.

## Late contiguous addendum: 0652–0653

The final source refresh added `a934e39df2` (0652, safe recovery pid probe and wrapper test paths) and `9c943cbd2f` (0653, concrete pin-provider binding). Both public patches applied with `git am -3` in a new detached worktree at `90a63800fb`. Canonical runner results: recovery **28 passed** again, pin-provider **19 passed**, and targeted backup/doctor wrapper cases **2 passed**. That worktree was removed too. This extends the published inventory to **653 contiguous patches**, representing all **23** fork commits after `7f117fa465` through `9c943cbd2f`. The original 657-commit regeneration was followed by this two-commit append.

## Recovery reporting precision

The recovery implementation writes a JSON report file and emits markdown-style summary/card content. It does not create a separate `.md` file alongside every JSON report. The feature doc reflects that distinction rather than implying an artifact the implementation does not write.
