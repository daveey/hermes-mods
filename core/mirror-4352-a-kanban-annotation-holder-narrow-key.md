# Change-proportional kanban annotation holder and Windows custody fallback

Mirror snapshot 4352; ordinals 4180, 4203.

Entries:

- **4180**: perf(kanban): change-proportional annotation holder (card B)
- **4203**: fix(kanban): annotation host-copy custody falls back to msvcrt without fcntl

## Behavior

- **Annotation holder** (4180): the process that holds the annotation lease spent a large share of a core in its refresh loop because the exact activity fingerprint moves on most passes, mostly liveness churn. The holder now uses a narrow key per origin scope: liveness-normalized semantic tokens of replica rows linking the origin, ledger-bound owners and same-board publishers; the scoped origin-ledger snapshot with owner-current bits; gateway info/last_ok as 180 s buckets plus offline; own boards, shadows and waiting inputs exact. `activity_at`, `activity_kind` and a valid observation's hash restamp never move it; read errors count as moved (exact fallback); strict callers (task APIs, closeout, default remote links) keep the exact fingerprint. A 10 s per-scope floor follows an input-moved refresh; the 2 s probe and 20 s forced renewal are unchanged.
- The host copy is split into a small always-rewritten header and a content-addressed body written only when parts change (bounded retention). Foreground Kanban tool, HTTP and hosted-ASGI writes cue the scope after the outer commit and the cue is acknowledged only in a header written by a forced read that started after it. Display surfaces adopt a host copy up to 12 s old before exact probes.
- **Windows custody** (4203): without `fcntl`, host-copy custody takes an `msvcrt.locking` byte-0 lock on the same `.host.lock` file; readers lock exclusively and contention still raises and fails closed. A host with neither primitive still raises `ImportError` (no copy).

## How it works

- New modules `kanban_annotation_narrow.py`, `_reader.py`, `_semantic.py`, `_ledger.py`, `_dependencies.py` (incremental selector reporting changed keys, ledger snapshot memoized on `data_version`, per-row verdict caching), `_host.py`, `_mutation.py`, `_closeout.py` and `_disk.py`; touched wiring in `kanban_db_connect.py`, `kanban_session_activity.py`, `plugin_host_child.py`, `plugins_dispatch.py`, `web_server.py`, `web_routers/profiles.py`, `tui_gateway/methods_projects.py`, `tools/kanban_tools.py`. `scripts/benchmark_kanban_annotation_holder.py` is an operator benchmark. `kanban_annotation_storage.py` has `custody()`.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/kanban_annotation_closeout.py`
- `hermes_cli/kanban_annotation_dependencies.py`
- `hermes_cli/kanban_annotation_disk.py`
- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_ledger.py`
- `hermes_cli/kanban_annotation_mutation.py`
- `hermes_cli/kanban_annotation_narrow.py`
- `hermes_cli/kanban_annotation_reader.py`
- `hermes_cli/kanban_annotation_semantic.py`
- `hermes_cli/kanban_annotation_storage.py`
- `hermes_cli/kanban_db_connect.py`
- `hermes_cli/kanban_session_activity.py`
- `hermes_cli/kanban_session_activity_decode.py`
- `hermes_cli/plugin_host_child.py`
- `hermes_cli/plugins_dispatch.py`
- `hermes_cli/web_routers/profiles.py`
- `hermes_cli/web_server.py`
- `scripts/benchmark_kanban_annotation_holder.py`
- `tools/kanban_tools.py`
- `tools/kanban_tools_rejection.py`
- `tui_gateway/methods_projects.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/hermes_cli/test_kanban_annotation_closeout.py`
- `tests/hermes_cli/test_kanban_annotation_dependencies.py`
- `tests/hermes_cli/test_kanban_annotation_disk.py`
- `tests/hermes_cli/test_kanban_annotation_display.py`
- `tests/hermes_cli/test_kanban_annotation_holder_floor.py`
- `tests/hermes_cli/test_kanban_annotation_holder_narrow.py`
- `tests/hermes_cli/test_kanban_annotation_host.py`
- `tests/hermes_cli/test_kanban_annotation_host_split.py`
- `tests/hermes_cli/test_kanban_annotation_incremental.py`
- `tests/hermes_cli/test_kanban_annotation_ledger.py`
- `tests/hermes_cli/test_kanban_annotation_ledger_current.py`
- `tests/hermes_cli/test_kanban_annotation_mutation_routes.py`
- `tests/hermes_cli/test_kanban_annotation_narrow.py`
- `tests/hermes_cli/test_kanban_annotation_own_commit.py`
- `tests/hermes_cli/test_kanban_annotation_reader.py`
- `tests/hermes_cli/test_kanban_annotation_semantic.py`
- `tests/hermes_cli/test_kanban_annotation_semantic_rows.py`
- `tests/hermes_cli/test_kanban_annotation_storage_windows.py`
- `tests/hermes_cli/test_profile_scan_http_admission.py`
- `tests/hermes_cli/test_profiles_session_stamps.py`
- `tests/hermes_cli/test_profiles_sessions_scan_cache.py`
- `tests/hermes_cli/test_profiles_sessions_scan_serialization.py`
- `tests/hermes_cli/test_web_json_response.py`

## Applying

These entries are part of the complete ordered snapshot 4352. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 4352. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported. Some entries need no configuration; settings introduced are named under Behavior.

## Gotchas

- The commit message states the CPU budget was not met by narrowing alone; the forced 20 s renewal at the measured cost exceeded the holder budget and was reported rather than waived.
- Display adoption of a stale (<= 12 s) host copy is deliberate; strict paths stay exact.
- msvcrt has no shared lock mode, so Windows readers serialize.

## Plugin?

None.
