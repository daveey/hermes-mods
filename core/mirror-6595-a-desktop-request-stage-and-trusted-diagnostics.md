# Desktop request-stage evidence and trusted session-tasks diagnostic

Patches: 6063, 6064, 6122, 6123, 6124.

## Behavior

- Locally journaled, pathname-allowlisted observation of Desktop JSON polls from API call through headers/body/deadline and ASGI ingress through body-send; observer receipts precede JSON decoding. The pathname allowlist does not restrict requests to localhost. No bodies, queries or credentials are recorded.
- A trusted session-tasks diagnostic route reads a loader-owned identity independent of the mutable SDK host, reporting SDK_CHANGED on swapped methods. The main-process reader is inspector-only (no preload or renderer registration), checks a byte-pinned asset inventory and the window's navigation epoch around each read, and a guarded operation re-checks identity before and after it runs. The built-replay proof runner is repaired.

## How it works

request-stage producers in electron/hermes_cli.web_request_stages with rotating bounded IO; contrib identity registry and SDK custody.

## Code

Paths touched by these entries (non-test):

- `apps/desktop/electron/api-transport.ts`
- `apps/desktop/electron/json-fetch.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/request-stages.ts`
- `apps/desktop/electron/session-tasks-diagnostics.ts`
- `apps/desktop/scripts/k01-built-replay.mjs`
- `apps/desktop/src/contrib/diagnostic-primitives.ts`
- `apps/desktop/src/contrib/runtime-loader.ts`
- `apps/desktop/src/contrib/session-tasks-identity.ts`
- `apps/desktop/src/sdk/index.ts`
- `apps/desktop/src/sdk/runtime.ts`
- `apps/desktop/src/sdk/session-tasks-custody.ts`
- `apps/desktop/src/session-tasks-diagnostics.ts`
- `apps/desktop/vite.config.ts`
- `docs/request-stage-evidence.md`
- `hermes_cli/web_request_stages.py`
- `hermes_cli/web_server.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `apps/desktop/electron/request-stages.test.ts`
- `apps/desktop/electron/session-tasks-diagnostics.test.ts`
- `apps/desktop/scripts/k01-built-replay.test.ts`
- `apps/desktop/src/contrib/radio-plugin.test.ts`
- `apps/desktop/src/contrib/session-tasks-capture.test.ts`
- `apps/desktop/src/contrib/session-tasks-trusted-route.test.ts`
- `tests/hermes_cli/test_web_request_stages.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Instrumentation, not a transport fix.
- No installed-app claim.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: the diagnostics live in the desktop app and web server.
