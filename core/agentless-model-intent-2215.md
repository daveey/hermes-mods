# Persist model picks made after an agent build fails

Patches **2213–2214**.

## Behavior

A confirmed session model pick made with no built agent records durable ordered switch intent before publishing the in-memory override or retrying allocation. The next allocation can therefore replace an older exact route pin with the requested model. Persistence uses the stored session key, not the UI transport id.

Concurrent picks publish in commit order under the same lock used for build admission. A failed intent write or unavailable session store does not acknowledge an override that the allocator cannot honor. Build failures also log bounded chained call-site diagnostics rather than only a short error message.

## How it works

`_commit_agentless_switch` takes `agent_build_lock`, reads the stored runtime generation (zero for a new lazy row), and invokes the canonical `record_in_transaction` writer through the session database write transaction. Only after that succeeds does it publish `session['model_override']`. The agentless, session-pinned, non-one-turn branch of `_apply_model_switch` uses this helper; live-agent handling remains unchanged.

`log_build_failure` renders at most three exceptions in a cause/context chain, at most twenty innermost frames per exception and 300 characters of each message, capped to 8000 characters for its detached diagnostic. It omits source lines and frame locals, and logs a detached exception without retaining original traceback frames. Patch 2214 replaces a fake database in the failed-profile-resume regression with real `SessionDB` intent storage.

## Code

- `tui_gateway/model_switch.py`: durable agentless switch helper and setter integration.
- `tui_gateway/build_diagnostics.py`: bounded diagnostic renderer.
- `tui_gateway/session_registry.py`: call diagnostic logging on build failure.

## Tests

Supplied source coverage, **not executed by this documentation/mirror task**:

- `tests/tui_gateway/test_agentless_model_intent.py::test_failed_build_pick_rebuilds_new_exact_route`: composed setter, intent storage and real allocator replace the exact route; checks the stored key differs from the UI id and resume context survives.
- The same suite tests later-pick ordering, unconfirmed picks, failed writes, no-key/internal/one-turn/unavailable-store cases, generation handling and concurrent publication order.
- `tests/tui_gateway/test_build_diagnostics.py`: bounded innermost frames, no source-line/locals expansion, and wrapped-cause rendering.
- `tests/tui_gateway/test_tui_gateway_server.py::test_config_set_model_recovers_failed_profile_resume_after_build_completes`: real profile database, a second build, and persisted model/provider switch intent after the prior build completes.
- These are source regressions, not a live Desktop or production provider verification.

## Applying

Use only the complete ordered replay recipe in [README.md](README.md), including bridges and verification. Do not apply the subset independently. No new configuration is required.

## Gotchas

A session without a stored key cannot write durable intent; internal non-pinning changes do not create it. One-turn changes remain subject to existing agentless rejection. Diagnostic truncation is not secret redaction: the retained message prefix can still contain provider-supplied text. A durable pick is allocation authority, not proof that provider setup or the next build will succeed.

## plugin?

No. Ordered switch intent, exact-pin allocation and build admission are core runtime concerns.
