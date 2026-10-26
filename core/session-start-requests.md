# Durable plugin-authorized session starts

## Behavior

`session.start_request` takes exactly `{profile, namespace, start_request_id}` on the existing authenticated owner-routed RPC transport. It never accepts prompt text, a principal, gateway ID or destination chat. The server derives installation identity and principal from the authenticated transport, validates the profile, and resolves an immutable trusted intent through a registered in-process plugin service.

The owning profile's state.db commits one visible session, canonical first user row, unique first-turn ID, and scoped idempotency mapping in one writer transaction. Retries reuse that identity across process restarts. Queued requests may be claimed once; running/terminal requests never dispatch again. A crash after claim without a terminal receipt remains unknown_outcome, even if no runtime exists. This is at-most-once dispatch, not a promise that an ambiguous process crash completed its work.

Owner-local status reports stored and compression-resolved IDs, archival/existence, first-turn state and a durable active-turn lease observation. Archival, deletion, another transcript turn or compression before claim cancels the queued start instead of inserting into a changed chat. Turn-isolated runtimes are explicitly unsupported for new starts, while already-claimed identities remain reconcilable. Missing authenticated principal is refused.

## How it works

Import `StartOwner`, `StartIntent`, and `get_service` from `tui_gateway.session_start_requests`. In the owning serve runtime, call `get_service(profile)` and `register(owner, namespace, resolver)`. The resolver receives owner and request ID, reauthorizes every start/status call, and returns `StartIntent(title=..., text=..., source='desktop')`. Registration returns a disposer; duplicate registrations are rejected. The owner must match core's installation ID (install:<installation ID>), canonical profile, and provider-prefixed transport user identity. Do not derive any of those from untrusted request fields.

Trusted in-process callers use the same service.start(owner, namespace, request_id), and collectors use service.status(...). A standalone collector does not acquire permission to run a chat by importing this module: register/read in its authorized owner context and route execution to the owning runtime. No unauthenticated callback, new courier or arbitrary client-supplied code is introduced. Plugins retain their repair-to-start foreign links; core owns chat and first-turn identities.

The GatewayStartRuntime adapter reserves the first turn before publishing the live record, stages the already-committed user row for the existing persistence-adoption path, builds the normal agent, and calls the existing _run_prompt_submit lifecycle. Terminal callbacks persist completion/cancellation/failure and disable automatic crash continuation. A concurrent preexisting human runtime is not overwritten. A refused retirement spawn is a failed start, not an indefinitely busy phantom.

## Code

- tui_gateway/session_start_requests.py: store, scoped resolver contract, runtime adapter and importable plugin entry.
- tui_gateway/methods_session_start_requests.py: authenticated RPC and owning-runtime service factory.
- tui_gateway/methods_session.py: normal method registration. Handler split is required by the existing file-size health cap.
- tui_gateway/contracts/sessions.py and apps/shared/src/gateway-contract.{generated.ts,openrpc.json}: typed wire contract.
- tests/tui_gateway/test_session_start_requests.py.

## Tests

33 focused tests pass: concurrent real SQLite handles; transactional rollback; lost-response and restart reuse; immutable payload conflicts; profile/gateway/principal separation; same session ID across two gateways; compression/archive; active lease and unknown outcome; registration/disposal and revoked authorization; unsupported runtimes; queued recovery; schedule failure without replay; RPC input-injection and missing-auth refusals; reserve-before-publication and refused-spawn guards; real _run_prompt_submit with a fake model adopting the committed user row and committing a terminal receipt.

63 focused plus existing session/profile/identity/lineage/submit-row/lease/contract tests passed before handler extraction; 37 focused+contract tests passed after extraction. `python3 scripts/check --commit HEAD`: all 12 checks pass. Ruff 0.15.10 and precommit checks pass; gitleaks staged scan found no leaks.

No live runner repair, real Desktop click, live provider/LLM, or separate collector-process integration is claimed by these tests. Those plugin/client E2E gates are separate.

## Applying

Core commit 7f8355d35ed9cb1aa2f86e4edc45b55da1a6a0be is on fork main. Apply in the ordered public patch series, or update to a fork main containing it. This mirror publishes it as patch 0287 in the full ordered series. No config flag changes or plugin activation are made by this core commit.

## Gotchas

Back up state.db before enabling a new writer. The ledger is additive and survives chat deletion; never remove it to retry a request. Request payloads must remain immutable. Unknown outcomes require explicit reconciliation, not a fresh automatic send. A new authorized retry must have a new plugin request identity. The active-turn flag is a durable lease observation, not proof of process health or of first-turn completion. Resolver callbacks are trusted Python, not a sandbox, and must enforce plugin policy.

Merged and deployed are different. Mirror verification did not restart or deploy any gateway. Exact-head CI is owned by the implementation lane, not asserted here. The public mirror applied all 260 patches at merge-base `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`; its canonical serial test run passed all 33 session-start tests plus 25 runtime-safety regressions.

## Why not a plugin alone?

The prior creation key cache was process-local, and ordinary drafts deliberately had no durable row. Only core can atomically associate a visible session and first user turn before scheduling and adopt that row into its existing turn lifecycle. Plugins still own their domain policy, repair records and frontend actions.


## Trusted metadata and authenticated SSH ownership (0376–0377)

Source commits `5c31e92ca8` and `b598614d83` are merged. The following implementation test results are the source handoff, not a live rollout claim.

### Behavior

Trusted in-process resolvers can return StartIntent(title=..., text=..., source='desktop', metadata={'runner_repair_id': repair_id}). Metadata defaults to empty, accepts at most 32 string pairs, and bounds keys to 1–64 characters and values to 0–1024 characters. Construction snapshots the dictionary into an immutable mapping. RPC clients still supply only profile, namespace, and start_request_id; no client prompt, metadata, identity, or destination-session fields are accepted.

The durable start ledger adds metadata_json with an empty-object default. Metadata, session, first transcript row and start mapping commit atomically. lookup/status return decoded metadata, including when status follows a compressed session's current ID. Nonempty metadata participates in intent conflict detection; empty metadata preserves the previous digest so old retries remain valid.

A Desktop SSH backend launched using the validated private one-shot token file binds its stable ownership directory ID to the current credential. For loopback, ungated requests with that matching credential, REST attaches request.state.session with provider='desktop-ssh' and user_id=<32 lowercase hexadecimal ownership ID>. WebSocket admission stamps the equivalent auth_identity; RPC obtains principal desktop-ssh:<ownership ID>. Rotating token or spawn nonce does not change the ownership directory identity. Ordinary local/stdio callers, malformed bootstraps, public binds, and query-token-only downloads gain no new principal. Gated OAuth authentication retains its existing identity.

Resuming an existing stored session preserves its persisted owner, including a NULL owner. It does not silently assign ownership to the current SSH transport. New authenticated starts still use the transport identity.

### Code

- tui_gateway/session_start_requests.py: validated immutable metadata, additive ledger migration, atomic accept and metadata lookup/status.
- tui_gateway/contracts/sessions.py and generated shared contracts: metadata result field.
- hermes_cli/main_dashboard.py and main.py: identity source from successfully validated token-file bootstrap.
- hermes_cli/web_server.py and web_server_chat.py: credential-bound REST/WS stamping.
- tui_gateway/methods_session.py and server.py: preservation of persisted ownership during cold/deferred/eager/lazy resume.
- tui_gateway/methods_session_params.py: extracted parameter helpers, explicitly published into the server namespace.

### Tests

Focused combined suite: 111 passing tests, including metadata migration, rollback, immutable snapshots, bounded inputs, retry compatibility/conflicts, compression; actual middleware/WS upgrade identity parity and negative auth gates; bootstrap file validation; existing start service tests; typed-contract freshness; nine persisted-owner regressions covering all four resume paths and new transport-owned starts. All 12 scripts/check gates pass.

The two dashboard middleware asset-guard failures reproduce identically on the implementation base (20 passed/2 failed) and head (20 passed/2 failed); these are not counted as passing. This is not a claim of a green full Python suite. Final independent static security review found no remaining blocking findings.

### Applying and gotchas

Update to a containing fork main or apply the corresponding ordered public patches. Back up state.db before activating new writers. Restart only through the supported safe deployment path: an imported Python module is not upgraded by a disk-plugin reload. No active gateway should be restarted to obtain acceptance.

Metadata is a start-ledger contract, not a new arbitrary session-metadata database API. Plugins must use the core owner-local lookup/status service and keep their foreign repair-to-start association. The SSH identity is an authenticated Desktop ownership slot, not a human account or globally shared fake principal; it must not be reconstructed from untrusted request fields.

Actual Desktop owner-route readback and real repair-chat duplicate-click acceptance are separate rollout gates. No live chat was started by these tests.
