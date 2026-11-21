# Worker drain: image-bound baseline, cold startup and no-ingress gate

Mirror snapshot 5406; ordinals 5349, 5350, 5351, 5352, 5359.

Entries:

- **5349**: Bind worker drain baselines to process and image provenance
- **5350**: Add cold drain startup and root baseline installation boundary
- **5351**: Use canonical runtime root for daemon drain identity
- **5352**: Reject torn drain identities and pin init package provenance
- **5359**: Gate drain daemon exemption on no message ingress and bind root observer

## Behavior

- **5349**: root-only baseline capture from an image-authored service manifest (boot, instance, node epoch, PID/start identities, atomic writes) and a startup-only kernel-sealed capability primitive.
- **5350**: cold gateway startup mints/revokes the capability before foreign code runs; an explicit root-only installation boundary never discovers an allowlist.
- **5351**: the canonical runtime root is used for daemon drain identity.
- **5352**: process identity is sampled twice and rejected when PID/start, UID, cgroup or executable path differs between samples; this detects observed transitions, not a transactional procfs snapshot. Init-package provenance is pinned; legacy cron fail-open revokes the daemon capability before allowing dispatch.
- **5359**: a bounded no-ingress gate (running effective config and adapter lists empty, sealed inventory receipt, revalidation, revoke before adapter construction) replaces unbounded ingress queue custody; a root observer binds boot/instance/epoch/latch/attempt around its scan and never exempts arbitrary in-process callers. Developer guide added.

## How it works

- `worker_drain_baseline.py`, `worker_drain_capability.py`, `worker_drain_census.py`, `worker_drain_baseline_cli.py`, `worker_drain_ingress.py`, `worker_drain_observer.py`, `worker_drain_result_inventory.py`, `gateway_drain_startup.py`, gateway startup/adapters, plugin loader and tool registry hooks, spawn inventory JSON, developer doc.

## Code

Shipped non-test paths (union over these entries):

- `hermes_cli/worker_drain_baseline.py`
- `hermes_cli/worker_drain_capability.py`
- `hermes_cli/worker_drain_census.py`
- `agent/turn_facade.py`
- `hermes_cli/gateway.py`
- `hermes_cli/gateway_drain_startup.py`
- `hermes_cli/plugins_loader.py`
- `hermes_cli/worker_drain_baseline_cli.py`
- `hermes_cli/worker_drain_spawn_inventory.json`
- `tools/registry.py`
- `cron/scheduler_tick.py`
- `gateway/run_adapters.py`
- `gateway/run_startup.py`
- `hermes_cli/worker_drain_ingress.py`
- `hermes_cli/worker_drain_observer.py`
- `hermes_cli/worker_drain_result_inventory.py`
- `website/docs/developer-guide/worker-drain-baseline.md`

## Tests

Source coverage includes manifest/package and PID/start binding, sealed capability controls, cold-start/revocation ordering, trusted baseline writer paths, changed-identity rejection and cron degradation. The no-ingress tests cover configured/live primary and secondary adapters, missing or invalidated inventory, and revocation before adapter construction; observer tests cover stale request bindings, changes during observation, and ordinary/imported callers being unable to exclude themselves. These are fixture/kernel-control tests in the source, not evidence of a deployed worker's idle state.

Test files shipped by these entries (none executed in this mirror documentation pass; no mirror pass result is claimed):

- `tests/hermes_cli/test_worker_drain_baseline.py`
- `tests/hermes_cli/test_worker_drain_capability.py`
- `tests/hermes_cli/test_worker_drain_census_manifest.py`
- `tests/hermes_cli/test_worker_drain_producer.py`
- `tests/hermes_cli/test_worker_drain_baseline_cli.py`
- `tests/hermes_cli/test_worker_drain_daemon_startup.py`
- `tests/hermes_cli/test_worker_drain_roundtrip.py`
- `tests/hermes_cli/test_worker_drain_source_scan.py`
- `tests/hermes_cli/test_worker_drain_writer_paths.py`
- `tests/tools/test_worker_drain_dispatch.py`
- `tests/hermes_cli/test_worker_drain_cron_degrade.py`
- `tests/hermes_cli/test_worker_drain_identity_races.py`
- `tests/hermes_cli/test_worker_drain_init_provenance.py`
- `tests/hermes_cli/test_worker_drain_ingress_gate.py`
- `tests/hermes_cli/test_worker_drain_observer.py`

## Applying

These entries are part of the complete ordered snapshot 5406. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered 5406 patch series (every segment, in order, with its bridge patches) followed by the mandatory repair overlay that README names. They are not standalone patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported.

## Gotchas

- 5349 explicitly leaves production startup/installer integration pending; 5350 subsequently adds the cold-start hook and installation CLI. Those code boundaries are not evidence that an image installed or activated them. 5359 explicitly claims no production baseline or idle ZERO; no worker activation is implied here.
- Installed observer/producer integration is image-authored outside core.

## Why core instead of a plugin

The drain census gates core startup and update-time admission; the image/installer side is separate, but the fence itself must be enforced from inside the process.
