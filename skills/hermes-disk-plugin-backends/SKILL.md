---
name: hermes-disk-plugin-backends
description: "Use when adding Hermes disk-plugin REST backends."
---

# Disk-plugin backend contracts

- Inspect installed `hermes_cli/plugins.py`, `tools/registry.py`, and a local `dashboard/manifest.json` before implementing; API details change across releases.
- Export `router = APIRouter()` from the manifest's `api` Python file. Hermes mounts it under `/api/plugins/<plugin-id>` and owns authentication; exercise an isolated FastAPI app with that prefix before live integration.
- Load sibling modules with `importlib.util.spec_from_file_location`, register in `sys.modules` before execution, and remove failed entries. Hyphenated plugin directories and standalone dashboard imports make ordinary package-relative imports unreliable. Do not mutate `sys.path`.
- Register agent tools through `ctx.register_tool`; handlers accept `(args, **context)` and return JSON strings. Prefer trusted context session IDs over model-supplied IDs; require explicit identity dimensions that the host does not guarantee.
- Resolve storage through `get_hermes_home()`, with environment-aware fallback only when the import is unavailable. Keep databases outside plugin source and make paths injectable for tests.
- Use explicit adapter factories, not broad module scans. Keep imports side-effect free; absent adapters must remain unconfigured rather than returning demonstration data.
- Do not treat desktop-local connection IDs as server ownership identity. Return a persistent backend identity from inventory and validate it on registry writes; test restart stability and foreign-ID rejection.
- Keep provider network calls AND SQLite persistence outside the snapshot lock. A database writer can stall a commit even after collection finishes. Use bounded database waits; retain fresh in-memory data and expose a sanitized persistence error if saving fails. Reproduce contention with a second connection holding `BEGIN IMMEDIATE`, then prove concurrent inventory reads return before rollback.
- Coalesce refreshes with a separate nonblocking lock, throttle manual refreshes, and apply TTL on normal reads rather than only forced refreshes. Mark concurrent startup snapshots loading/incomplete; test concurrency with events rather than sleeps.
- Persist provider `last_success_at` separately from observation/response time; update only after a fully successful collection, retaining it across partial results, failures and restarts.
- Restore cache for factory-discovered provider names during registration, not only constructor defaults, so newly supported providers retain stale inventory after restart.
- Validate types as well as field names before serialization and again on cached-data load. An allowed `ssh` or `error` key containing an arbitrary object can carry credentials through an otherwise allowlisted schema. Preserve approved scalar caveats, reject nested objects, and require timezone-aware timestamps. Test synthetic secret sentinels against both API output and persisted cache.
- Sum monetary strings with sufficient local Decimal precision, not the default context; arbitrary accepted input precision can otherwise round a supposedly complete total. Bound coefficient/exponent sizes against unbounded expansion, preserve unknown rates, and keep currencies separate. Include a regression whose exact sum exceeds the default precision.
- Keep exact ownership matching distinct from identity existence validation. A syntactically valid profile/session tuple is not proof that it exists in Hermes; verify through a trusted host accessor or explicitly report that validation as incomplete.
- When parallel agents own separate adapter modules, keep the shared factory (`providers/__init__.py`) owned by the integrator. Register new adapters there last, then update factory-set assertions; a provider-ID or key-format mismatch between adapter and core usually shows up only at integration.
- Before enabling, replay the real path end to end: load the plugin `__init__` via importlib, build `InventoryService` on a temp dir, call `discover_adapters()`, run `inventory(refresh=True)` under the host's actual serve interpreter, and print only per-provider status/counts/summary. Unit suites with injected collectors miss live redirects, slow collectors, and default-kind mislabels.
- Test with isolated homes and `TMPDIR` under the Hermes scratch directory; never enable plugins or restart a live gateway merely to validate registration.
