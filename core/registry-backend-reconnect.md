# Generation-scoped registry backend reconnect

Patches: **0309 0310 0315 0317 0318 0319 0320 0321 0322 0323**.

## Behavior

Desktop can explicitly reconnect one owned, non-primary Linux registry SSH backend without restarting the gateway daemon, primary backend, another connection or another profile. Discovery returns an opaque resident generation; reconnect acts only on that exact target. Busy, unsupported, unowned, unknown-idle or replaced residents fail closed.

Preload exposes `connections.reconnectTarget(connectionId, profile)` returning `{connectionId, profile, generation} | null`, and `connections.reconnect(target)` returning `{ok, generation?}` or rejecting on lifecycle/transport failure. Successful replacement publication is not proof that application RPCs work.

## How it works

The connection gate blocks configuration mutation and new bootstrap publication. A resident-specific fence also prevents direct pool eviction. The authenticated remote retirement protocol prepares, commits or cancels a backend-owned admission fence atomically with turn admission. An earlier idle poll is not equivalent.

Termination is bounded and proves Linux process identity independently of the managed Python runtime, so deletion of that runtime does not invalidate ownership proof. Ambiguous commit/termination retains the exact-resident fence. An explicitly authorized retry with the **same target** reconciles it; a live owner must prove ownership and commit retirement, or a read-only process probe must prove the captured owner exited. Unknown process state never authorizes a kill. Failed replacement bootstrap is not success; after confirmed retirement its fence is released for ordinary dialing to retry.

## Patch identities and repeated changes

| First identity | Repeated source identity | Role |
|---|---|---|
| 0309 | 0318 | Scoped reconnect coordinator, service, HTTP retirement client and preload API. |
| 0310 | 0319 | Bounded termination and dispatch fences. |
| 0315 | 0320 | Direct pool-eviction fence. |
| 0317 | 0321 | Recovery after ambiguous commit with a stopped owner. |

These are distinct source commits representing repeated changes in merged history. Both identities remain documented in the ordered export; **do not blindly apply a duplicate hunk or drop its provenance**. The mirror owner determines the full-series apply adaptation. 0322 adds the atomic retirement/admission race test; 0323 extracts reconnect wiring and documents the safety contract.

## Code

`apps/desktop/electron/registry-reconnect.ts`, `registry-reconnect-service.ts`, `registry-retirement-http.ts`, `registry-stopped-owner.ts`, `registry-reconnect-wiring.ts`, `pool-stop.ts`, `remote-lifecycle.ts`, `main.ts`, `preload.ts` and `src/global.d.ts`.

Authoritative source design: `website/docs/developer-guide/registry-backend-reconnect.md` at mirrored source `6385cf3622a002fc9ad7cd182b53f2e346358fa5`.

## Tests

Source suites, **not executed by this documentation pass**:

- Electron `registry-reconnect.test.ts`: generation qualification, replaced residents, concurrent dials, ambiguous commit and exact-generation recovery.
- `registry-reconnect-service.test.ts`: composed lifecycle, unsupported platforms, primary alias refusal, busy resident, bounded termination, direct eviction and stopped-owner recovery.
- `registry-retirement-http.test.ts`: exact authenticated descriptor and transport failure versus refusal.
- `registry-stopped-owner.test.ts`: local live/exited processes with synthetic SSH transport, not a real remote reconnect.
- `registry-reconnect-wiring.test.ts`: exact dial correlation and ownership/transport classification.
- `tests/hermes_cli/test_backend_retirement.py`: authenticated endpoint and threaded atomic admission-versus-freeze race (0322).

Run Electron tests using `npx vitest run electron/<suite>.test.ts` from `apps/desktop`, and Python tests using `bash scripts/run_tests.sh tests/hermes_cli/test_backend_retirement.py`. Executed full-series and selected test results are recorded in [mirror verification](mirror-377-verification.md). No live reconnect or bundle installation is claimed.

## Applying

Apply in full-series context with the duplicate treatment above. The running Electron bundle must expose both preload methods, and the already-running remote serve must support authenticated SSH ownership protocol v1 plus retirement prepare/commit/cancel. Updating source files does not upgrade either running process.

## Gotchas

Only eligible Linux remote residents are supported; Windows/Darwin are refused before preparation. Do not replace refusal with profile-only `recycleBackend`, a managed update, a broad process kill, or a fresh-generation retry loop. Record source merge, released bundle, installed/running bundle, remote capability and a separately authorized live operation independently.

## Plugin relationship

A renderer integration can call the preload API, but Electron lifecycle coordination and remote atomic admission fences require core. A disk plugin cannot safely simulate them with a status poll and process termination.
