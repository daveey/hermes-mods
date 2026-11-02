# Prewarm session-list Kanban annotation at serve boot

Patches: 1141.

## Behavior

- Serve starts a daemon prewarm of the session-list Kanban annotation during application lifespan startup.
- The read is skipped under `HERMES_TEST_ISOLATION`.

## How it works

Boot starts the existing annotation snapshot read before reconnect polling. A first request can find the completed snapshot or join the read already in flight; this does not create a second annotation implementation.

## Code

- `hermes_cli/kanban_annotation_prewarm.py`
- `hermes_cli/web_server_app.py`

## Tests

Source regression coverage:

- `tests/hermes_cli/test_kanban_annotation_prewarm.py`
- `tests/hermes_cli/test_serve_unloaded_sweeper.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

The source describes consumers including session lists and session detail. No fresh REST latency benchmark or live Desktop reconnect burst is claimed here.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.

## Follow-up patch (1583): build the all-profiles project tree at boot

Patch: `patches/1583-perf-serve-build-the-all-profiles-project-tree-at-bo.patch` (`ca4fa11665`, `perf(serve): build the all-profiles project tree at boot, after the kanban annotation prewarm [daveey]`).

**Behavior.** After a backend restart, the first `GET /api/profiles/projects/tree` paid every cold cost of a new process inside the Desktop's reconnect burst: the lazy `tui_gateway.server` import, one `git` root probe per session cwd, each profile's cold annotation scope and cold SQLite pages. The source reports ~3 s cold vs ~0.15–0.4 s warm, and one observed 4.02 s first tree read after restart, past the Desktop's 4–5 s request budget. The boot prewarm thread now builds the tree once, right after the annotation prewarm, so the first Desktop poll reads warm caches.

**How it works.** `kanban_annotation_prewarm.start_annotation_prewarm()` now runs `_boot_prewarm()` on the same daemon thread (`kanban-annotation-prewarm`; no new thread, startup stays nonblocking): `prewarm_annotation()` first, since the tree reads that annotation, then `projects_tree_prewarm.prewarm_project_tree()`. That calls the route function `profiles.get_profiles_projects_tree()` itself, so the build goes through the route's single-flight cache: a poll arriving mid-build waits for it instead of starting a second cold scan. The prewarm never raises (failures are logged at debug; the first real read reports its own error). It is still skipped under `HERMES_TEST_ISOLATION`.

**Code.** New `hermes_cli/projects_tree_prewarm.py`; `hermes_cli/kanban_annotation_prewarm.py` (`_boot_prewarm`, thread target).

**Tests** (from the patch; not run for this mirror). `tests/hermes_cli/test_projects_tree_prewarm.py`: `test_prewarm_builds_the_tree_through_the_route`, `test_prewarm_never_raises`, `test_boot_thread_warms_the_annotation_then_the_tree` (boot order; the source owner reports it RED on the base), `test_first_tree_poll_after_the_prewarm_does_not_scan_again` (single-flight reuse). `tests/hermes_cli/test_kanban_annotation_prewarm.py::test_start_runs_prewarm_on_a_daemon_thread` now stubs the tree prewarm. Real reconnect-burst latency after restart is not tested here; the source owner keeps live acceptance.

**Gotchas.** The tree prewarm runs only after the annotation prewarm finishes on the same thread, so a slow annotation delays it. It moves the first build earlier and does not make it cheaper: a Desktop poll that arrives before the build finishes still waits for it.
