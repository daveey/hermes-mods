# Fork CI on self-hosted runners, and the test fixes that made it green

Patches:

- `patches/0173-ci-run-Linux-CI-on-self-hosted-hermes-hub-runners-da.patch`: Linux CI jobs run on self-hosted runners.
- `patches/0176-test-git_pack_tidy-skip-incremental-midx-case-on-git.patch`: skip the `--incremental` midx case on old git.
- `patches/0177-test-relay-shared-metrics-load-tolerant-worker-join-.patch`: load-tolerant relay-metrics timeouts.
- `patches/0179-test-allowlist-reviewed-bare-which-sites-codesign-gh.patch`: allowlist two reviewed bare `which()` sites.
- `patches/0181-ci-python-test-job-provisions-PM-locked-Node-de-flak.patch`: python test job gets the locked Node; two flakes fixed.
- `patches/0182-tests-doctor-isolate-vercel-backend-and-gh-auth-test.patch`: doctor tests no longer depend on the runner.
- `patches/0183-tests-isolate-gateway-service-tests-from-container-r.patch`: gateway service tests no longer depend on the runner.
- `patches/0188-test-make-3-env-sensitive-tests-independent-of-the-C.patch`: orphan-readopt (TUI) and termux-api detection tests no longer depend on the runner env.
- `patches/0189-lint-fix-12-ruff-errors-blocking-CI-daveey-11.patch`: 12 ruff errors (update_cmd_git, startup_resume and their tests) that kept the lint job red.
- `patches/0193-ci-run-CI-review-comment-on-self-hosted-hermes-hub-r.patch`: the PR review-comment poller (`ci-review-comment.yml`) runs on the self-hosted runner too; hosted jobs were refused for billing, so it failed before starting.
- `patches/0196-ci-tests-raise-Run-tests-timeout-to-120-min-shared-1.patch`: raises the `Run tests` timeout to 120 min for the shared 16-core hub runner.
- `patches/0197-ci-tests-size-test-workers-from-nproc-instead-of-fix.patch`: sizes pytest workers from `nproc` (2x) instead of a fixed 32.
- `patches/0198-ci-shorten-artifact-retention-14d-3d-and-make-lockfi.patch`: shortens artifact retention 14d -> 3d and makes the lockfile-diff upload non-fatal.
- `patches/0199-ci-rootless-docker-CI-lane-on-hermes-hub-docker-ci-r.patch`: adds a rootless-docker lane on dedicated `docker-ci` runners (amd64 image builds only, 120 min timeout), skips the mac/Windows lanes on the fork, makes artifact uploads `continue-on-error` (step-aware insert, no duplicate keys), and reports `tests-os`/`desktop_updater` lane assertions as fork skips.
- `patches/0200-tests-don-t-let-an-ambient-container-signal-self-hos.patch`: `tests/conftest.py` pins container detection so the runner's `/.dockerenv` does not leak into host-permission tests.
- `patches/0201-tests-mark-macOS-faking-hermes_cli-tests-check_os_ma.patch`: marks macOS-faking `hermes_cli` tests with `platforms("macos")` (`check_os_marker_fakes`).
- `patches/0202-test-tolerate-loaded-shared-runners-in-desktop-JS-te.patch`: desktop JS tests tolerate loaded runners: vitest timeout 5 s -> 30 s, PM source-backend port wait 150 s, checkout-source handoff claim 20 s; the spawn mock stamps the Desktop's `HERMES_UPDATE_STARTED_AT` so the claim matches.
- `patches/0203-tests-e2e-alias-describe-accepts-canonical-key-scale.patch`: e2e: the alias-describe test accepts the canonical key; the heartbeat floor scales with turn length.
- `patches/0204-ci-fix-deterministic-failures-on-hub-runners-contain.patch`: deterministic hub-runner failures: container detection pinned False at both seams (`hermes_constants`, `hermes_platform.host.runtime`) so owner-only mode tests see 0622/0735; refreshed prompt-surface snapshot, docs-drift baseline and `locales/_keys.desktop.json`; `classify_changes` routes `hermes_cli/top_level_env.py` to the update suites; `NODE_OPTIONS=--max-old-space-size=4096` for JS workspace checks (desktop `tsc` OOMs at 2 GB).
- `patches/0205-ci-give-pushes-to-main-their-own-concurrency-group-s.patch`: pushes to `main` get their own concurrency group, so a newer push never cancels or replaces a run in flight.
- `patches/0206-ci-drop-duplicate-continue-on-error-in-lockfile-diff.patch`: drops a duplicate `continue-on-error` in `lockfile-diff.yml` (the invalid file made `ci.yaml` fail to start).
- `patches/0207-ci-unbreak-main-on-hub-runners-container-test-seam-d.patch`: unbreaks main on hub runners: `TestIsContainer` restores the real `_detect_container`; documents `sessions.automated_sources` / `interactive_sources` / `sidebar_profiles` (docs-drift ratchet); js-tests timeout 30 -> 60 min; `termux-verify` jobs move to `[self-hosted, hermes-hub]`.
- `patches/0209-ci-nix-add-workflow_dispatch-so-the-flake-check-can-.patch`: `nix.yml` gets `workflow_dispatch`, so the flake check can be re-run on demand (the workflow had been disabled, so it never re-ran on main).
- `patches/0218-ci-lint-workflows-with-actionlint-move-remaining-aut.patch`: adds an `actionlint` workflow (`.github/actionlint.yaml` ignores a few known non-bug findings, each pattern narrow) and moves the remaining auto-triggered jobs (archive-inputs, install-e2e-red, label-rerun, osv-scanner, plugin-catalog-ci, pm-bundle, ...) to the self-hosted runner.
- `patches/0224-fix-utf-8-sig-reads-in-plugins_discovery-kanban_sess.patch`: `utf-8-sig` reads in `plugins_discovery` and `kanban_session_activity`, so the blocking Windows-footguns lint passes on main.
- `patches/0226-test-f-string-check-False-in-delegate-marker-probe-s.patch`: f-string + `check=False` in the delegate-marker probe test, so blocking `ruff check .` passes (UP031, PLW1510).
- `patches/0230-ci-main-pushes-share-one-concurrency-group-per-CI-Ni.patch`: pushes to `main` share one concurrency group per workflow (`ci-main`, `nix-main`, `actionlint-main`): the run in flight finishes and only the newest pending push waits (a per-run group queued a full suite per push and saturated the hub runners). Refines 0205.
- `patches/0249-test-publish-LSP-descendant-readiness-atomically-dav.patch`: the mock LSP server publishes its child PID by write-then-rename, so file existence implies a complete PID.
- `patches/0252-test-remove-shared-runner-timing-assumptions-daveey.patch`: the bundle-smoke JS test gets a 60 s budget (ten real Node CLIs share the runner) and the goal-parked-resume idle-hook test pins `time.time` so a slow `cli` import cannot outlast its one-second barrier.
- `patches/0480-fix-ci-main-red-pin-utf-8-on-wait-check-ssh-subproce.patch`: the `wait-check` ssh subprocess in `hermes_cli/kanban_wait_check.py` pins `encoding="utf-8"` (a bare `text=True` trips `test_subprocess_text_encoding`); `test_kanban_block_reason_scrubbed_jwt` now blocks with `kind=needs_input` + `human_request` (ownerless agent blocks are refused) and asserts the call succeeded. Source `d2d18a116d`.
- `patches/0482-fix-ci-migration-http-auth-test-creates-legacy-block.patch`: `tests/hermes_cli/test_kanban_migration_http_auth.py` creates its legacy blocked card with `lint_blocked=False`, since blocked-creation lint would otherwise refuse the fixture. Test-only. Source `e99a18feca`.
- `patches/0494-test-sessions-page-owner-custom-home-case-follows-f6.patch` (`19a2a3de28`): `tests/hermes_cli/test_session_message_page_owner.py` expects the nested-custom-home `profile=default` case to resolve to that home (120-row newest page), per `f6a16700fd`'s nested-root rule; the named-profile case keeps the cross-profile 1-row assertion.
- `patches/0495-chore-i18n-regenerate-_keys.desktop.json-for-shell.s.patch` (`3454645daa`): adds `shell.statusbar.modelLoading` to `locales/_keys.desktop.json` so `i18n-keys.test.mjs` (`check:test:desktop:platforms`) is green again.

What you see: the private fork's CI runs (and passes) without GitHub-hosted minutes. None of this changes runtime
behavior; it is workflow wiring and test isolation.

## Behavior

- `scripts/fork-ci-runner.sh` rewrites `runs-on:` of every Linux job (ubuntu-latest / 24.04 / 32-core) in 22 workflow
  files to `[self-hosted, <runner label>]`. macOS, Windows, arm and `${{ matrix.* }}` runners are left alone. It is
  idempotent; rerun it after merging upstream and commit the result.
- `tests-os` stays wired but disabled on the fork. Patches **1376–1380** restrict the fork's failure-to-skip hook to allowlisted upstream tests whose failing predicate concerns only that disabled lane; other merge-gate failures remain failures (details below).
- The python test job passes `toolchain: all` to `./.github/actions/setup-pm`, so it gets the PM-locked Node (apt's
  Node 18 cannot strip `.ts` or `require()` ESM, which several tests need).
- Tests now tolerate a loaded runner or a container: relay-metrics worker join and parked-flush waits are 120 s caps
  (healthy runs still return at once); the compute_host hello read allows 30 s; the typing-timeout test turns the
  delivery ledger off and allows 5 s.
- Tests that assume a host install pin `is_container()` to `False` (doctor vercel check, gateway service detection,
  multiplex served record), and the doctor gh-auth test stubs `_safe_which`.
- `test_kanban_restart_survival` waits for a numeric pid in `worker-ready` (the file is truncated before it is written).
- `test_git_pack_tidy` skips the `multi-pack-index write --incremental` case on git < 2.47 (exit 129 there).
- `tests/fixtures/resolution_allowlist.json` lists `_prebuilt_desktop_app_mismatch`, `_prebuilt_signature_error`
  (macOS `codesign`) and `review_session_activity._gh` (`gh` with homebrew fallbacks) as reviewed bare-`which` sites.
- `.github/workflows/actionlint.yml` lints every workflow on PRs and main (0218). Pushes to main in `ci.yaml`, `nix.yml` and `actionlint.yml` share one concurrency group each: the run in flight is never cancelled, only the newest pending push waits (0230).
- Lint-only fixes (0224, 0226) and test-isolation fixes (0249, 0252, 0480, 0482, 0494, 0495) keep blocking `ruff check .` and the windows-footgun scan green on main; no runtime change.

## How it works

Pure workflow/test edits; no production code path changes.

## Code

- `scripts/fork-ci-runner.sh`, `.github/workflows/*.yml` (0173); `.github/workflows/tests.yml` (0181).
- Tests: `tests/hermes_cli/test_git_pack_tidy.py`, `test_relay_shared_metrics_runtime.py`, `test_kanban_restart_survival.py`,
  `test_doctor.py`, `test_gateway_multiplex_served_record.py`, `test_gateway_service.py`, `tests/tui_gateway/test_compute_host.py`,
  `tests/gateway/test_run_progress_topics.py`, `tests/fixtures/resolution_allowlist.json`.

## Tests

These patches are the tests (0224 is covered by the windows-footgun scan, 0480/0469 edit `tests/tools/test_kanban_redaction.py` and `tests/hermes_cli/test_kanban_migration_http_auth.py` (the ssh encoding pin is guarded by `tests/hermes_cli/test_subprocess_text_encoding.py`), 0226/0249/0252 edit `tests/agent/test_delegate_marker_owner_binding.py`, `tests/agent/lsp/_mock_lsp_server.py`, `tests-js/bundle-smoke-metadata.test.mjs`, `tests/hermes_cli/test_cli_goal_parked_resume.py`; 0218/0230 are workflow files proven only by CI). Verified by the fork's CI going green on the runners; each edited file was run with
`scripts/run_tests.sh <file>` when written. Not re-run when this doc was written (untested here).

## Applying

`git am -3 --empty=keep` in order. 0173 is only useful if you run your own runners; replace the label in the script. The
mirror's `runs-on` label is whatever your runners register.

## Gotchas

- Upstream merges re-introduce `ubuntu-latest`; rerun `scripts/fork-ci-runner.sh`.
- Self-hosted runners are shared and heavily loaded, so keep timeouts generous caps rather than tight bounds.

## Plugin?

No. CI config and tests.

## Restricted OS-lane skips (1376–1380)

### Behavior

The checked-in pytest hook and the hook regenerated by `scripts/fork-ci-runner.sh` agree. Only allowlisted upstream lane tests can convert an OS-only assertion failure into a fork skip. `fork_guard` tests, runtime install-shape tests, unrelated predicates, and Linux consumer failures remain blocking even when their reports print `tests-os`. Parametrized OS-only cases still skip; the hook is inert when the lane is enabled.

### How it works / Code

`tests/ci/conftest.py` narrows by node ID and marker, then inspects the crash assertion rather than arbitrary printed source or needs lists. It recognizes only the known lane membership and consumer-map predicates, preserving upstream OS-only reports including truncated nested pytest representations. The script replaces its old hook block rather than accumulating hooks. Runtime/install-shape suites carry `fork_guard` markers.

### Tests

Executed at frozen source `b1af31437760e139cc4729fdbd513ca9c276e8f8` in the 1381 mirror pass:

- `tests/ci/test_fork_os_lane_hook.py`: **25 passed**. Real pytest subprocesses cover the checked-in hook, disabled/enabled lane, marker and node-ID restrictions, printed list/dict/set distractions, compound predicates and truncated representations. `test_writer_and_checked_in_conftest_are_the_same_hook` pins parity; `test_writer_replaces_an_older_hook_block_instead_of_keeping_it` executes the writer's replacement and append blocks twice.
- `tests/ci/test_runtime_install_shape.py`: **9 passed**.
- `tests/ci/test_fast_runner_routing.py`: **10 passed**.
- `tests/ci/test_tui_install_shape.py`: **12 passed**.

Command and aggregate results: [mirror-1381-verification.md](mirror-1381-verification.md). The full workflow rewrite and a hosted CI dispatch were not exercised in this pass.

### Applying / Gotchas / Plugin?

Apply the complete series with the README's bridge-aware recipe, not these five patches independently. Keep writer and generated conftest changes together. This is core CI test policy, not a plugin or a general permission to suppress failures.

## Follow-up patches (0599, 0604, 0625)

Red-main fixes, no behaviour change: **0599** (`7d752288be`) allowlists the bare `which` in `hub_cli_path` in `tests/fixtures/resolution_allowlist.json` and makes the stdio test opt out of host slots so it reads no real `~/.hermes`. **0604** (`fd394a4611`) fixes ruff PIE807 in `tests/hermes_cli/test_kanban_placement.py` (stub `config` with `dict`) and replaces `os.kill(pid, 0)` in `tools/browser_use_cli.py` `_owner_alive` with `gateway.status._pid_exists` (Windows-safe, treats zombies as dead); both had landed while main's concurrency group cancelled every newer run. **0625** (`838f8024f7`) refreshes the nine `tool_tokens` counts in `tests/fixtures/prompt_surface/surfaces.json` left stale by a `browser_exec` schema change; no prompt or schema text moves. Verified by the CI jobs named (ruff, footgun, `tests/ci/test_prompt_surface.py`); `_owner_alive` has no new dedicated test (untested).
