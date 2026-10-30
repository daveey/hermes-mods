# CI apt/Nix-sandbox, code_health merge judging, termux APT keys and test deflakes, 1159-1201

Patches:

- `patches/1159-ci-js-tests-apt-get-update-before-installing-Electro.patch`
- `patches/1167-test-cli-reproduce-review-exit-with-the-real-daemon-.patch`
- `patches/1169-test-gateway-wait-for-the-owner-s-idle-before-moa-in.patch`
- `patches/1170-fix-tests-deflake-prepared-compaction-pass-joins-wai.patch`
- `patches/1171-test-orphan-reap-stub-the-dashboard-argv-reader-so-a.patch`
- `patches/1172-test-desktop-remote-row-archive-carries-the-fence-id.patch`
- `patches/1173-test-desktop-await-preference-handoff-I-O-instead-of.patch`
- `patches/1178-test-tui_gateway-poller-wiring-tests-wait-on-the-con.patch`
- `patches/1179-fix-ci-refresh-JS-apt-indexes-and-require-real-Nix-s.patch`
- `patches/1182-test-processes-owner-pid-markers-are-published-by-re.patch`
- `patches/1187-test-desktop-non-null-the-found-PATCH-call-so-the-de.patch`
- `patches/1196-code_health-judge-a-merge-commit-against-each-parent.patch`
- `patches/1199-test-desktop-transform-sync-handoff-s-store-graph-in.patch`
- `patches/1201-fix-termux-import-protected-APT-signing-keys-attende.patch`

Four non-test changes (js-tests apt refresh, Nix sandbox requirement, code_health merge judging, termux key import) plus test-only deflakes and reproductions.

## Behavior

- 1159: `js-tests.yml` runs `apt-get update -qq` before installing the Electron deps, because the runner image's baked package lists are stale after first-boot mirror rewrite.
- 1179: that step becomes `apt-get update --error-on=any` (a partial index fetch fails the job). `nix.yml` adds `sandbox = true` and `sandbox-fallback = false` so the flake check fails closed instead of building unsandboxed.
- 1196: `code_health` judges a merge commit against each parent; a finding blocks only if it blocks against every parent. New `--also-base REV` (repeatable) flag.
- 1201: termux `sign()` imports a passphrase-protected key file with its passphrase and without `--batch`, so a random S2K salt containing `(`/`)`/space sequences is no longer stored corrupted ("Bad passphrase" flake). Unprotected keys keep the old stdin import.
- Test-only: 1167 (review-exit test warms the real `DaemonThreadPoolExecutor`), 1169 (waits for the session owner to be idle before `/moa`), 1170 (pass joins use a 60 s deadline and assert the thread ended), 1171 (stubs `_dashboard_cmdline_for_pid`), 1172 (new remote-row archive test), 1173 and 1199 (sync-handoff waits on I/O; store graph warmed in `beforeAll`), 1178 (poller waits via `wait_until` with 60 s), 1182 (owner pid markers published by rename; receipt wait fails fast if the owner dies), 1187 (non-null assertion for a TS18048 typecheck error).

## How it works

- 1196: `_findings_against()` measures one base. If any finding blocks against the first base, the others are measured and `_relax_to_most_permissive()` clears `blocking` on findings not still blocking against each other parent. `main()` uses `gitio.merge_parents()` for a merge head; with `--base` (CI) extra bases come only from `--also-base`.
- 1201: `_gpg_run(..., batch=False)` swaps `--batch` for `--no-tty` (loopback pinentry, passphrase on fd 0). Cause per the commit: unattended import stores the key unconverted and GnuPG 2.4's key-file writer drops spaces after `(`, `)` or a space.
- 1169: the helper `idle()` waits for the admission rows AND `session.list` showing `running` false, replacing the retry-the-refusal loop.

## Code

`.github/workflows/js-tests.yml`, `.github/workflows/nix.yml`, `scripts/code_health/{cli,gitio}.py`, `scripts/termux/stage_apt_repo.py`; tests under `tests/{agent,hermes_cli,gateway,tools,tui_gateway,ci,scripts}` and `apps/desktop/src/{api,lib/ui-preferences}`.

## Tests

- 1179: `tests/ci/test_nix_workflow_sandbox.py::test_flake_check_requires_the_sandbox` (config only; it does not prove a runner has namespaces); `tests/scripts/ci/test_js_system_packages.py::test_js_provisioning_refreshes_indexes_and_fails_closed` (Linux, update status 0 and 100, fake `apt-get`). 1159 alone has no test.
- 1196: `tests/scripts/test_code_health_cli.py`: `test_merge_growth_within_one_parents_cap_does_not_block`, `test_merge_growth_over_both_parents_blocks`, `test_non_merge_commit_is_judged_against_its_base_only`, `test_main_uses_a_merge_commits_parents_automatically`.
- 1201: `tests/scripts/test_stage_apt_repo.py::test_protected_key_whose_s2k_salt_has_spaces_still_signs` (skipped without gpg; needs `cryptography`; the commit says red on the base, green with the fix).
- 1172: `apps/desktop/src/api/sessions.test.ts` new case for the remote-row archive. 1187 fixes its typecheck.
- The rest are deflakes of existing tests: `test_local_review_without_cached_agent.py`, `test_prepared_compaction.py`, `test_orphan_reap_home_scope.py`, `sync-handoff.test.ts`, `test_kanban_notify_poller.py`, `test_terminal_owned_jobs.py`, `test_cli_exit_cancels_background_review.py`. Commit messages cite loaded-runner runs; none were re-run for this mirror.

## Applying

Apply the complete numbered series at the merge-base with `git am -3 --empty=keep`; entries are not independent (1179 edits the line 1159 adds; 1172/1187 and 1173/1199 touch the same test files). See `README.md`.

## Gotchas

- `sandbox-fallback = false` makes `nix.yml` fail on runners without user namespaces.
- 1179's commit message lists an extra planner fix (full-tree checkout); the diff does not contain it.
- 1196 relaxes only findings that block; allowed findings are untouched. Without a merge head and without `--also-base`, behavior is unchanged.
- 1201's non-batch import still never prompts (`--no-tty`, loopback), but only applies when `TERMUX_APT_GPG_PASSPHRASE` is set.

## Plugin?

No, core change.
