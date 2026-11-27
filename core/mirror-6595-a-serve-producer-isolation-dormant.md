# Serve producer isolation (dormant panel producer and admission)

Patches: 6050, 6051, 6052, 6053, 6054, 6055, 6056, 6057, 6058, 6059, 6060, 6061.

## Behavior

- Adds bounded escaped JSON framing, atomic request reassembly, boot-qualified deadlines and stable inherited family-lock custody for a panel producer exec child.
- Strict admission observations, a host census, a launch gate that enrolls supported entrypoints before producer imports, and enrollment bound to live panel custody.
- Restart is fenced before provider import, refresh failures revoke the prepared projection, exhausted restarts reap without cleanup tracebacks, and admission identity is unified.

**Inert (producer only):** the producer capability is dormant in the source: nothing starts an external producer and no production path activates guarded mode. The pre-import launch gate is not inert, though: on Linux, `supported_launch(role)` is called from the gateway runner import, dashboard/serve and agent startup in `hermes_cli/main.py`, the unmanaged-gateway spawner, `plugin_host_child` and `tui_gateway/entry.py`. It records a launch receipt (`participants.json` under `serve-producer-v1` in the default Hermes root, default 10 s gate wait) and can raise if enrollment is unsafe, malformed or busy. Enrollment tickets are always unguarded, and the census classifies them as legacy.

## How it works

serve_producer_* modules provide protocol, leases, custody, admission, census, enrollment, identity, process and runtime; the launch gate hooks entrypoints.

## Code

Paths touched by these entries (non-test):

- `gateway/run.py`
- `hermes_cli/gateway_runtime_start.py`
- `hermes_cli/main.py`
- `hermes_cli/plugin_host_child.py`
- `hermes_cli/serve_producer_admission.py`
- `hermes_cli/serve_producer_census.py`
- `hermes_cli/serve_producer_custody.py`
- `hermes_cli/serve_producer_enrollment.py`
- `hermes_cli/serve_producer_identity.py`
- `hermes_cli/serve_producer_leases.py`
- `hermes_cli/serve_producer_process.py`
- `hermes_cli/serve_producer_protocol.py`
- `hermes_cli/serve_producer_runtime.py`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `tui_gateway/entry.py`
- `website/docs/developer-guide/serve-producer-isolation.md`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/gateway/test_agent_cli_visibility.py`
- `tests/hermes_cli/test_serve_producer_admission.py`
- `tests/hermes_cli/test_serve_producer_admission_boundaries.py`
- `tests/hermes_cli/test_serve_producer_census.py`
- `tests/hermes_cli/test_serve_producer_collectors.py`
- `tests/hermes_cli/test_serve_producer_custody.py`
- `tests/hermes_cli/test_serve_producer_descendants.py`
- `tests/hermes_cli/test_serve_producer_enrollment.py`
- `tests/hermes_cli/test_serve_producer_identity.py`
- `tests/hermes_cli/test_serve_producer_launch.py`
- `tests/hermes_cli/test_serve_producer_launch_boundaries.py`
- `tests/hermes_cli/test_serve_producer_leases.py`
- `tests/hermes_cli/test_serve_producer_projection_unavailable.py`
- `tests/hermes_cli/test_serve_producer_protocol.py`
- `tests/hermes_cli/test_serve_producer_review_boundaries.py`
- `tests/hermes_cli/test_serve_producer_review_namespace.py`
- `tests/hermes_cli/test_serve_producer_review_startup.py`
- `tests/hermes_cli/test_serve_producer_runtime.py`
- `tests/hermes_cli/test_serve_producer_runtime_failures.py`
- `tests/hermes_cli/test_serve_producer_shared_home_startup.py`
- `tests/hermes_cli/test_serve_producer_teardown.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Dormant producer: no production startup path exists; start_for_fixture refuses callers outside pytest and serialized enrollment tickets stay unguarded. Ordinary entrypoint enrollments classify as legacy; only the exact exec broker can be promoted by a live process-local BrokerEnrollment that proves current family custody, not by a persisted guarded bit. Launch enrollment itself runs on the supported entrypoints (Linux only; other platforms skip it).
- A COMPLETE fixture census is not a host activation receipt.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes serve launch, process custody and admission inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
