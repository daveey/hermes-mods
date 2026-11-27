# Early native-sessions capture at serve boot and update-admission hold

Patches: 6036, 6087, 6088, 6089.

## Behavior

- On Linux headless serve with HERMES_FACTS_SESSIONS enabled, native sessions capture starts overlapped with imports and is bound before service warmup, retaining capture custody through startup failures, signals and app adoption. Non-headless launches, disabled facts and non-Linux hosts skip this early-capture path.
- Warmup shutdown is retained on exceptional exit and honors ignored signals; early-session signal installation is skipped off the main thread (serve may run from a worker thread after an update hold clears), while drain handlers install only where legal.
- A successor unit in always-on rollover checks the installation-root update-admission hold before marker waiting and again after port discovery, before publishing warming state, so its boot budget is not spent inside the serve fence. When the hold is set (or unreadable) the roll is deferred with a logged reason and the current backend is kept for normal supervision retry; the serve-side fence stays authoritative for a hold that arrives later.
- While standalone services warm, the boot-services middleware admits health and native selector requests; websocket requests during warmup get the connect consumed and an accept before the retryable close.

## How it works

sessions_early_boot and web_boot_services own the pre-import capture and warmup barrier; dashboard_server_start and web_server_app wire them in; always_on_rollover consults the hold at the two points described.

## Code

Paths touched by these entries (non-test):

- `hermes_cli/always_on_rollover.py`
- `hermes_cli/dashboard_server_start.py`
- `hermes_cli/main.py`
- `hermes_cli/sessions_early_boot.py`
- `hermes_cli/sessions_native_startup.py`
- `hermes_cli/web_boot_services.py`
- `hermes_cli/web_server.py`
- `hermes_cli/web_server_app.py`

## Tests

Entry-path behavior above is source-reviewed but **untested in this review**. The files below identify source regression targets, not proof that this review exercised those entrypoints.

Source regression coverage named in these commits (inspected from the source repository; **source coverage was NOT executed in this mirror run**):

- `tests/hermes_cli/test_always_on_rollover.py`
- `tests/hermes_cli/test_sessions_early_boot.py`
- `tests/hermes_cli/test_web_boot_services.py`
- `tests/hermes_cli/test_web_server_launch_env_freeze.py`

## Applying

These entries are a dependency unit within the numbered series: follow the complete segmented [README Applying recipe](README.md#applying), including its hash-pinned bridges, ancestry markers and mandatory post-replay escape repair. Entries here build on earlier and neighbouring patches and are not promised to install independently. See [the inventory and policy in README.md](README.md).

## Gotchas

- Permanent or dangling holds do not launch or change the active unit record.
- Signal handlers cannot be installed off the main thread; the code path must degrade quietly there.

This page documents source behavior only; it makes no deployment, rollout or live-operation claim.

## Why core, not a plugin?

Core, not a plugin: this changes serve boot sequencing and always-on rollover inside the host application itself (not a plugin-owned extension point), so it cannot be delivered as an add-on.
