# Core contract/spec trace (staged backend only)

Scope: inventory.py, ownership.py, __init__.py, dashboard/plugin_api.py against ~/.hermes/plans/compute-rentals.md. This is functional/spec tracing, not the separate code-quality review, live-cloud validation, or desktop acceptance.

## PASS in core

- Normalized identity/key is provider/account/region/provider_id; per-provider dictionaries deduplicate resources. Registry uses gateway/profile/durable session string as composite owner; multiple bindings do not duplicate inventory rows.
- Empty GET gateway ID resolves to a persistent SQLite-backed backend UUID, returned as top-level gateway_id. Restart retains it; independent state directories differ. POST and DELETE reject foreign backend IDs. Empty session selects nothing; profile/session matching is exact.
- Global and selected-session totals use Decimal strings separated by currency. Unknown/invalid prices remain null and make totals incomplete.
- Failed/partial collections retain stale previous records; complete success replaces them. Cache survives restart, including providers discovered after constructor defaults.
- GET refresh=false collects at startup and after nominal 30-second TTL (measured from completed collection). Explicit refresh=true has a 2-second cooldown. Overlapping refreshes coalesce. Collection does not hold the snapshot lock: other requests receive refreshing=true and incomplete totals; initial provider state is loading, not ready/zero-complete.
- build_collectors() is preferred; legacy create_adapters() remains supported. Relative imports work under an isolated package despite hyphenated disk-plugin path. Factory mappings expand provider coverage automatically.
- Allowlisted string rate_note/state_detail survive normalization; arbitrary fields are dropped.
- Core exposes only inventory and local registry operations. Trusted tool context overrides supplied session identity. Runtime state remains under resolved Hermes home; fixture/probe state used scratch only.

## Gaps / limits observed

- The first request triggering a scan still waits for sequential collectors in the FastAPI worker thread. Other requests remain responsive. This is not a background refresh worker; one slow provider can delay freshness for later providers. Adapter-owned timeouts remain important.
- Refresh coalescing/throttle is process-local, not a distributed/multi-worker lock.
- Provider tag/label ownership reconciliation from the architecture paragraph is absent. Only explicit registry bindings exist; arbitrary external launches remain unassigned.
- Durable session IDs/profile strings are syntactically validated but not checked against Hermes session storage. Tool-supplied trusted session context is used when present; otherwise explicit IDs are trusted inputs.
- No dedicated last_success timestamp exists. observed_at is provider-supplied snapshot timing and the top-level observed_at is response generation time, not collection success time.
- No structured fixed-term commitment field exists; current core can expose rate caveats through rate_note but cannot represent a separate commitment amount.
- Slurm resource_kind/parent_machine_key/jobs metadata and machine/job counting semantics are not implemented in this core. No speculative schema was added; expanded-provider integration remains separate.
- Exception messages are redacted, unknown fields dropped, but allowed adapter strings (error, scope, ssh, URLs, notes) are trusted, not generically secret-scrubbed. Renderer/log secret exclusion still requires adapter and UI review.
- No live inventory calls, app activation, session-switch DOM checks, config changes, or gateway restarts performed. These full-plan acceptance criteria are not claimed as passed.

## Execution evidence

Strict RED/GREEN targeted tests observed missing gateway_id, missing TTL/collection behavior, wrong factory preference, stripped notes, and lost dynamic-provider restart cache before fixes.

Final command:
`pytest -q plugins/compute-rentals/tests/test_contract.py plugins/compute-rentals/tests/test_api.py plugins/compute-rentals/tests/test_inventory.py plugins/compute-rentals/tests/test_ownership.py`
Result: **16 passed in 0.37s**.

Final full Python command:
`pytest -q plugins/compute-rentals/tests`
Result: **32 passed in 0.37s**. An earlier concurrent snapshot had one EC2 adapter failure (empty successful region plus failed region); the later run passed without this task editing provider files/tests.

Installed-host interpreter the Hermes agent venv interpreter imported fastapi/pydantic/ruamel.yaml and discovered actual collectors `ec2`, `sfcompute`, `vast` (and any extra providers) without invoking them. It lacks pytest, so test execution used the existing `pytest` executable at another venv.

Existing test_api identity fixtures and corresponding expected identity values changed from literal 'g' to service.gateway_id to match the new contract; all previous behavioral assertions remain. No provider/UI files were edited.
