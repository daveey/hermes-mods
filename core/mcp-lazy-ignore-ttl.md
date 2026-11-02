# MCP `lazy_ignore_ttl`: lazy servers start from a `ttlMs: 0` schema cache

Patches: 1697

- `patches/1697-feat-mcp-lazy_ignore_ttl-lets-lazy-servers-start-fro.patch`

Related: [mcp-finalization-quiet-exit.md](mcp-finalization-quiet-exit.md) (1693, 1702) is the other MCP change in
this range.

What you see: a new per-server option `mcp_servers.<name>.lazy_ignore_ttl: true`. With `lazy: true`, Hermes registers
the server's tools from its on-disk schema cache at startup even when the server declared the cached `tools/list`
expired, and starts the server only on the first tool call. Without it, chrome-devtools-mcp (which answers
`tools/list` with `ttlMs: 0`) could never start lazily and was spawned eagerly in every agent process (about 110 MB
each, per the commit message).

## Behavior

- `lazy_ignore_ttl` (boolish, default `false`) applies only when `lazy` is true. Then a cache entry whose config
  fingerprint matches is used whatever its `ttl_ms` and `written_at`.
- The fingerprint still gates the entry: a config change (a different fingerprint) misses, and the server connects
  eagerly as before.
- `lazy_ignore_ttl: true` without `lazy: true` has no effect: the server connects eagerly.
- Without the option, an entry past its TTL (including `ttlMs: 0`) is still a miss, and a lazy server falls back to an
  eager connect, as before.
- Documented in `website/docs/user-guide/features/mcp.md` and `website/docs/reference/mcp-config-reference.md`.

## How it works

- `tools/mcp_schema_cache.get_cached_entry(server_name, fingerprint, *, ignore_ttl=False)`: the `expired` calculation
  is skipped when `ignore_ttl` is true. The fingerprint check runs first.
- `tools/mcp_tool_discovery._resolve_server_lazy_ignore_ttl(config)` parses the option with `_parse_boolish`.
  `_register_lazy_from_cache` passes it as `ignore_ttl` for each server that `_resolve_server_lazy` marks lazy.

## Code

`tools/mcp_schema_cache.py`, `tools/mcp_tool_discovery.py`, `website/docs/user-guide/features/mcp.md`,
`website/docs/reference/mcp-config-reference.md`.

## Tests

- `tests/tools/test_mcp_schema_cache_ttl.py::test_ignore_ttl_serves_expired_entry`: a `ttl_ms=0` entry misses by
  default, hits with `ignore_ttl=True`, and still misses for another fingerprint.
- `tests/tools/test_mcp_lazy_start.py::TestLazyIgnoreTtl`, against a real on-disk cache entry with `ttl_ms: 0` and
  `register_mcp_servers` (registration/discovery/loop calls mocked):
  - `test_ttl_zero_entry_with_ignore_ttl_registers_lazily`: registers from cache, no discovery, nothing run on the MCP loop.
  - `test_ttl_zero_entry_without_ignore_ttl_stays_eager`: no cache registration, one eager run.
  - `test_fingerprint_mismatch_stays_eager` (with and without the option): stale fingerprint → eager.
  - `test_ignore_ttl_without_lazy_stays_eager`.

The mirror pass did not rerun these tests.

Untested: the commit message's claim that the first real tool call connects live and write-through refreshes the
cache entry (existing lazy-start behavior; no test in this patch); the memory saving; a real chrome-devtools-mcp
process.

## Applying

Needs the lazy MCP start (`lazy`, `_register_lazy_from_cache`) and the TTL-aware schema cache (`ttl_ms` /
`written_at` in `tools/mcp_schema_cache.py`). Otherwise standalone.

## Gotchas

- The option tells Hermes to trust a schema the server said not to cache. Until the first real call reconnects, the
  model sees the cached tool list, which may be out of date if the server's tools changed without a config change.
- A lazy server still needs one prior live connect to fill the cache; with no entry it connects eagerly.

## Plugin?

No. MCP discovery and the schema cache are core; plugins cannot change how `mcp_servers` entries are registered at
startup.
