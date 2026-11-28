# Startup import ordering, deferred plugin API sweep and update bytecode warmup

Patch coverage: 6608, 6609, 6618, 6661, 6662, 6840, 6841, 6937.

## Behavior

- **Managed dependency precedence (6608, 6609).** `pm.environments.activate_dependencies` now calls `pm.dependency_imports.select_wheel_sources(project_root, selected)` after it strips site-packages/root from `sys.path` and before `site.addsitedir` runs `.pth` files. For every distribution that has a `*.dist-info` directly under the checkout root **and** a same-named, non-editable (`direct_url.json` not marked editable) distribution in the selected managed environment, the importable modules listed in **both** RECORDs (`.py` and extension suffixes, `__init__` mapped to its package) are routed by a `_DuplicateWheelFinder` placed ahead of `PathFinder` on `sys.meta_path` to the managed copy. Nothing is moved or deleted and `sys.path` order is unchanged; unrelated modules, namespace portions, core sources and editable members keep their normal resolution. If the managed copy of an owned module is missing the import raises instead of falling back to the checkout copy. If an owned module was already imported from under the checkout (and not under the selected environment), activation raises `RuntimeError` instead of evicting it. Repeated activation replaces its own finder rather than stacking and leaves foreign finders alone. 6609 uses the interpreter's real source/extension suffix list (no longer truncating dotted filenames at the first dot) and accepts an already-imported module whose origin is under the selected environment even when that environment sits inside the checkout root.
- **Update warmup (6618).** In `update_cmd_maint._sweep_bytecode_after_update`, only if the stale-bytecode sweep and fingerprint stamp succeeded (the `else` branch), `precompile_checkout(root)` runs `sys.executable -I -S -m compileall -q -j 1` over the fixed roots (`hermes_cli`, `agent`, `tools`, `tui_gateway`, `gateway`, `cron`, `pm`, `plugins`, `acp_adapter`) plus top-level `*.py`, skipping symlinks, with a 30 s timeout and output discarded. Non-zero exit, timeout or spawn errors are logged at debug level (or print a one-line "skipped" note) and never fail the update. It is called only from update completion, not at launch; a cache written by a different interpreter/cache tag is harmless but does not speed the later interpreter. The spawn inventory JSON gains an entry for the new module.
- **Model wizard exports on demand (6661, 6662).** `hermes_cli.main` no longer imports the model-setup flow and provider-setup helpers at module load. A new `main_command_exports.COMMAND_ATTR_SOURCES` maps those private names to owner modules; `main.__getattr__` imports the owner on first read and caches the real object with `globals().setdefault` only on success (an import failure propagates and is retried). Internal call sites use `_command_export(name)`, which prefers an existing module global (so test monkeypatches on `hermes_cli.main` still win) and otherwise resolves through `__getattr__`. The frozen updater surface is a separate table and is untouched. 6662 adds from-import and partial-owner recovery tests.
- **Deferred plugin API sweep (6840, 6841).** `dashboard_server_start.run_server`, for headless launches only, calls `web_boot_services.defer_plugin_api_sweep()` before importing `web_server`. It returns false (and the launcher logs a warning, with the old eager behavior unchanged) if `web_server` is already imported. When deferred, `_mount_plugin_api_routes` only builds the lifecycle registry, registers the hot-mount fallback and sets `app.state.plugin_api_sweep_owed`. The sweep itself then runs off the event loop inside `services_lifespan` via `_services_with_plugin_apis`: in the split-boot path (listener-ready and sessions-facts present, no gateway runner) that is after the listener is up; in the other branch it runs at services start. Routes are captured per plugin (`include_router(..., deferred_routes=...)`), router lifespans are entered, and only then are routes spliced in ahead of the hot-mount fallback and the OpenAPI schema is invalidated. Hosted proxy routes (no in-process lifespan) are withheld until services enter. A plugin whose router lifespan fails is skipped, stopped (in a worker thread; a failing close is logged and retried at shutdown), and its name is added to `app.state.plugin_api_refused`, which both the hot-mount path and `_mount_one_plugin_api` honor so the first request cannot re-import it without its lifespan. State yielded by router lifespans is stored in `app.state.plugin_lifespan_state` and `BootServicesMiddleware` copies it into each request scope. Eager dashboard and embedded callers are unchanged.
- **Test fixture (6937).** Test-only: the desktop source-backend RPC fixture retries only the boot gate's explicit 1013 "warming" websocket close every 100 ms under the same 15 s deadline, and rejects other closes immediately. Production readiness is unchanged.

## How it works

See above; entry points are `activate_dependencies`, `_sweep_bytecode_after_update`, `hermes_cli.main.__getattr__`/`_command_export`, and `run_server` → `services_lifespan`.

## Code

`pm/dependency_imports.py`, `pm/environments.py`, `hermes_cli/update_bytecode.py`, `hermes_cli/update_cmd_maint.py`, `hermes_cli/worker_drain_spawn_inventory.json`, `hermes_cli/main.py`, `hermes_cli/main_command_exports.py`, `hermes_cli/web_boot_services.py`, `hermes_cli/web_server_dashboard.py`, `hermes_cli/dashboard_server_start.py`, `hermes_cli/fact_plugin_mount.py`.

## Tests

Read from source diffs; **not executed here**; no fresh run and no native or end-to-end green claim.

- `tests/pm/test_dependency_source_ownership.py` (managed duplicate without stealing the namespace, RECORD-only ownership, editable/source survival, missing owned module refuses, loaded checkout code refuses, repeated install preserves foreign finders, malformed metadata refuses), `tests/hermes_cli/test_code_attestation_deps.py`, `test_update_bytecode_precompile.py` (real isolated compile, best-effort failures, no spawn on empty tree, warms only after successful stamp, launch fingerprint match does not compile), `test_main_command_exports.py`, `test_plugin_api_deferred_sweep.py` (fallback position, headless-only deferral, lifespan ownership, request gating, failed-lifespan refusal and failing close, hosted-proxy withholding, lifespan state, closed registry, already-imported server), and touched `test_doctor_command_install.py`, `test_source_launcher_publication.py`.
- `apps/desktop/electron/source-backend.test.ts` (6937): warming-close retry, terminal-close rejection and deadline cases, plus the existing real PM source-launcher cases.
- Real-listener behavior of the deferred sweep on a deployed host and the real update flow are **untested** here.

## Applying

Apply in ordinal order; 6841 builds on 6840, and 6937 follows the deferral.

## Gotchas

- Health 200 means the listener is bound, not that services are ready.
- Known and out of scope per the source: wrong 404 after waiting behind a mount lock, per-request forced rescan.
- Duplicate-wheel routing applies only where the checkout holds a dist-info that the managed environment also has; it does not remove the stray files.

## Why core, not a plugin?

Bootstrap, update and dashboard startup are core.
