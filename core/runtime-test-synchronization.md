# Runtime regression test synchronization and compatibility

Patches: 1136 1140 1146 1147 1148 1155 1157.

## Behavior

These entries change test synchronization or fixture compatibility, not production behavior:
- 1136 checks shielded-failure forwarding without depending on CPython’s removed logging behavior.
- 1140 synchronizes orphan-held pipe regressions on child exit.
- 1146 synchronizes same-turn compression stall fallback.
- 1147 holds stalled lookup responses open by trickling bytes; bounded waits allow the runtime deadline to be the terminating condition.
- 1148 patches `GatewayAgent`, the constructor actually used by CLI initialization, for background-review kwargs assertions.
- 1155 waits for the review completion queue event with a 120-second ceiling and gives teardown drain 30 seconds.
- 1157 retries the cold `/moa` probe’s running-agent refusal, with a 45-second bound, after `/review` admissions have settled.

## How it works

Tests wait for the event that establishes readiness rather than assuming a short elapsed deadline implies readiness. The source commit for 1140 includes a long historical subject/body; its actual diff is confined to the two process-registry test files listed below.

## Code

- `tests/tools/process_registry_fixtures.py`
- `tests/tools/test_process_registry.py`
- `tests/agent/test_compression_stall_fallback.py`
- `tests/agent/test_review_engine.py`
- `tests/hermes_cli/test_source_check.py`
- `tests/hermes_cli/test_cli_exit_cancels_background_review.py`

## Tests

Source regression coverage:

- `tests/tui_gateway/test_tui_gateway_loop_noise_ws_keepalive.py`
- `tests/tools/test_process_registry.py`
- `tests/agent/test_compression_stall_fallback.py`
- `tests/hermes_cli/test_source_check.py`
- `tests/hermes_cli/test_cli_exit_cancels_background_review.py`
- `tests/agent/test_review_engine.py`
- `tests/gateway/test_local_review_without_cached_agent.py`

See [mirror-1157-verification.md](mirror-1157-verification.md) for the exact files freshly executed for this publication and their results. Files listed here are source coverage references, not a claim that every file was rerun.

## Applying

Apply the complete numbered series at the frozen merge-base with `git am -3 --empty=keep`. Earlier patches include a curated integration overlay; individual entries are not promised to install independently. See [the inventory](README.md) and [verification report](mirror-1157-verification.md).

## Gotchas

Only the targeted files actually named in the verification report are freshly run. This document does not claim a full suite or real model-backed slash-command probe.

## Plugin?

No. This changes core lifecycle, admission, discovery, routing or its tests/workflows rather than a plugin-owned extension point.
