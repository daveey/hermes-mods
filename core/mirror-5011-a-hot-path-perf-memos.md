# Performance: hosted-room readiness memo, profile roster fill recheck, annotation inventories, loop-pass skips, picker pass memo, board row reuse

Mirror snapshot 5011; ordinals 4370, 4389, 4401, 4408, 4425, 4435, 4436, 4437.

Entries:

- **4370**: perf(hosted-rooms): memoize positive read-store readiness
- **4389**: test(hosted-rooms): prove readiness exception closes connection
- **4401**: fix(profiles): recheck completed memo before starting a fill
- **4408**: Share annotation narrow replica inventories across origin scopes
- **4425**: H2 remainder: card_change_pass idle skip + hot-forever probe inputs (C11)
- **4435**: perf(picker): resolve the Codex login and provider-profile ids once per model.options pass
- **4436**: perf(kanban): reuse commit-fenced board rows and typed overlays
- **4437**: Register dashboard row P1 accounting boundary

## Behavior

- **Hosted rooms (4370, 4389)**: `list_rooms` polls every few seconds and re-ran every readiness probe on each fresh connection. A proven True result is cached per process keyed by resolved path, device, inode and schema version (bounded to 128 paths, locked, bracketed by file identity so a replaced file cannot inherit its predecessor's memo; False is never cached). A test proves readiness exceptions (including KeyboardInterrupt) close the connection.
- **Profile roster (4401)**: the memo is rechecked under the flight lock so a delayed miss does not recompute a fill another leader already completed; copying stays outside the lock.
- **Annotation narrow replica inventories (4408)**: origin scopes share a bounded path-level inventory, raw/token snapshot acquisition is serialized, only 64 generations of changed keys are kept, and lagging selectors union retained changes; gaps, rebuilt generations, errors and resets rebuild conservatively. Strict readers and the 10 s / 20 s bounds are unchanged; this is a partial CPU reduction, not full acceptance.
- **Loop-pass skips (4425)**: the card-change pass skips boards whose durable task-event cursor is unchanged without opening a connection and does one outcome refresh per chat per pass; guarantee, killed-notice and notice probes mirror their consumers' lookback/retry windows and no longer key on store revisions that bookkeeping constantly moves. Pending deferrals, in-flight claims and unknown inputs still disable every skip.
- **Picker (4435)**: a per-pass memo (context variable closed on exit) resolves the stored Codex login and models-dev provider-profile ids once per `model.options` pass; nothing survives the pass, so login changes or registry changes are visible on the next call and the payload is byte-identical.
- **Dashboard rows (4436, 4437)**: board rows and typed run overlays are reused when a commit key (schema and data versions sampled under the refresh lock) is unchanged, with live values still projected on every GET and typed run-value signatures packed with marshal; the accounting boundary for skipped passes is registered in the boundary-lint baseline.

## How it works

- `gateway/hosted_rooms.py`, `tui_gateway/profile_roster_cache.py`, `kanban_annotation_inventory.py` / `_narrow.py` / `_reader.py`, `tui_gateway/kanban_unloaded_gates.py` and `notice_fanout.py`, `agent/pass_memo.py` (+ `models_dev.py`, `codex_service_tiers.py`, `hermes_cli/inventory.py`), and `plugins/kanban/dashboard/board_*.py`.

## Code

Shipped non-test paths (union over these entries):

- `agent/codex_service_tiers.py`
- `agent/models_dev.py`
- `agent/pass_memo.py`
- `gateway/hosted_rooms.py`
- `hermes_cli/inventory.py`
- `hermes_cli/kanban_annotation_inventory.py`
- `hermes_cli/kanban_annotation_narrow.py`
- `hermes_cli/kanban_annotation_reader.py`
- `hermes_cli/kanban_diagnostics.py`
- `owned/cache/plugin_hooks.json`
- `plugins/kanban/dashboard/board_read.py`
- `plugins/kanban/dashboard/board_rows.py`
- `plugins/kanban/dashboard/board_run_history.py`
- `scripts/boundary_lint_baseline.json`
- `tui_gateway/kanban_unloaded_gates.py`
- `tui_gateway/notice_fanout.py`
- `tui_gateway/profile_roster_cache.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/agent/test_picker_pass_costs.py`
- `tests/gateway/test_hosted_rooms_ready_cache.py`
- `tests/hermes_cli/test_kanban_annotation_shared.py`
- `tests/plugins/test_kanban_board_commit_reuse.py`
- `tests/plugins/test_kanban_board_p2.py`
- `tests/plugins/test_kanban_projection_cost.py`
- `tests/plugins/test_kanban_run_history.py`
- `tests/tui_gateway/test_h2_gate_mutations.py`
- `tests/tui_gateway/test_h2_killed_probe.py`
- `tests/tui_gateway/test_h2_remainder_c11.py`
- `tests/tui_gateway/test_profile_roster_cache_singleflight.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- Reported micro-benchmarks are component CPU on the author's machine, not live acceptance; live budgets were assessed elsewhere.
- Memos are per-process; every cache has a fall-back to exact recomputation on any doubt.
- The run-value signature bytes are compared in-process only and never persisted.
