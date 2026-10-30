# Serve watchdog, plugin discovery and exception-narrowing cost fixes

Patches: 1116, 1117, 1128.

## Behavior

- The `hermes serve` parent-death watchdog verifies the process start marker once per 30 s window; polls in between use the cheap pid-liveness check, which still sees a dead parent (1116).
- A bare `except Exception` in that watchdog is narrowed to `(OSError, ValueError, TypeError)` (1117).
- `list_memory_provider_names()` skips `ast.parse` of plugin modules whose text names neither `MemoryProvider` nor `register_memory_provider` (1128).

## How it works

1116 adds `_make_orphan_check(...)` with `_MARKER_RECHECK_S = 30.0`, injectable clock and probes. 1128 adds a substring precheck, which is semantics-preserving because both detection branches need one of those strings.

## Code

- `hermes_cli/web_server_lifecycle.py` (1116, 1117)
- `plugins/memory/__init__.py` (1128)

## Tests

Tests below were identified from the patch source in this documentation pass; they were **not executed** here. Where a patch lists no test name, it edited existing tests or touched untested code and nothing is claimed for it. `tests/hermes_cli/test_serve_parent_watchdog.py::test_orphan_check_pays_for_the_start_marker_probe_once_per_window` and `::test_orphan_check_still_sees_a_dead_parent_between_marker_probes`. `tests/plugins/memory/test_discovery_sources.py::test_provider_via_aliased_import_past_8kb_is_still_discovered` and `::test_non_provider_plugin_dirs_are_not_ast_parsed`. 1117 adds no test.

## Applying

These patches belong to the ordered fork series that follows an upstream merge. Apply the whole series with `git am -3 --empty=keep` from its recorded base; each patch depends on the ones before it, and no independent application of this subset was tested. The ported tests only make sense against the post-merge upstream names they reference.

## Gotchas

Between marker probes a recycled pid is not detected until the next window (up to 30 s). The CPU figures in the commit messages are the author's measurements.

## Could this be a plugin instead?

No: both are core code paths.
