# Provider host re-check before 'misconfigured'

Patch **2080**.

## Behavior

`wait_for_network` used to return `misconfigured` (ERROR plus a spent retry) when the provider host failed to resolve while the control host (github.com) did. On a waking or roaming machine the control name can answer from cache while a fresh lookup fails. Now, when the control host resolves:

- the provider host is re-checked `MISCONFIG_RECHECKS=2` times, 5 s of awake time apart; if it resolves the result is `online` (retry uncounted, one INFO line);
- `misconfigured` plus ERROR is returned only if it still fails **and** it never resolved earlier in this process **and** it is not a built-in provider host;
- otherwise one WARNING ("provider host stopped resolving, waiting") and the normal awake-time poll within the existing budget.

A genuinely mistyped custom base_url is therefore reported about 10 s later instead of immediately.

## How it works

`host_resolves` and the injected `resolve` path add successful names to `_RESOLVED_HOSTS`. `builtin_provider_hosts()` lazily builds a frozenset from the auth registry rows' inference base URLs and provider overlays (minus localhost/loopback); an import failure yields an empty set. `sleep_awake` sleeps in 0.2 s slices honouring `is_interrupted`.

## Code

- `agent/retry_utils.py`: `MISCONFIG_RECHECKS`, `MISCONFIG_RECHECK_S`, `_RESOLVED_HOSTS`, `builtin_provider_hosts`, `wait_for_network`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/agent/test_offline_wait.py`: built-in host failing once then resolving is online; custom host likewise; built-in host that keeps failing waits with no error; host resolved earlier in the process waits without error; `host_resolves` records successes; a full turn on a provider DNS blip with network up logs no misconfiguration. The existing typo-host test was relaxed from zero sleeps to at most two re-checks.

## Applying

Apply ordinal(s) 2080 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Requires the offline wait feature from [offline-dns-retry-wait.md](offline-dns-retry-wait.md) as a base. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- `_RESOLVED_HOSTS` is process-local and grows with distinct hosts; it is a small set in practice.
- The re-check delay is awake time, so a suspended machine does not burn it.

## Could this be a plugin?

No. The network wait sits inside the core turn retry path.
