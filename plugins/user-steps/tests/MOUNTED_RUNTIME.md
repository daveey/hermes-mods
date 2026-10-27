# Mounted-runtime integration boundary (candidate, not shipped)

## Recovery fix and evidence

Core `hermes_cli/web_server_dashboard.py::_PluginApiHotMount` calls
`_try_hot_mount_plugin_api` → `_mount_one_plugin_api` → `app.include_router` after
ASGI startup. Including an APIRouter then does **not** enter its lifespan.
The old plugin lifespan therefore did not recover an outbox in that path.

`dashboard/plugin_api.py` now shares a once-per-home recovery function between
lifespan and a router dependency. Core's outer `_plugin_route_secret_scope`
dependency enters the requested profile first; FastAPI copies that context into
the synchronous recovery dependency. Hot-mounted recovery happens automatically
on the first authenticated, enabled-plugin request **for each profile**, before
the handler reads the operation. It is not a process-wide background sweeper.
Cold, detached, busy, unsupported compute or otherwise unavailable owners remain
fail-closed; after that initial attempt they require the existing explicit Retry
or `/reconcile` once their owner becomes available. No session is resumed by this
change. Without a request to the hot-mounted profile there is no recovery tick.

`tests/test_mounted_runtime.py` exercises the actual core hot-mount fallback and
auth middleware, not a TestClient lifespan substitute. The recovery test was
observed red (`pending`, expected `delivered`) before the dependency was added.
It remounts after a durable decision and verifies automatic delivery, one durable
user row and one scheduled callback across subsequent reads.

The secondary-profile test uses real profile scope and separate SQLite stores,
with colliding stored-session IDs. A launch-profile request cannot find/deliver
the secondary operation. The secondary request recovers it through production
DecisionDelivery → PromptAdmission → server.handle_request, leaves launch
messages untouched, and same-operation retry does not schedule another callback.

## Event evidence versus Desktop acceptance

The secondary test attaches a recording owner transport **before** admission,
then invokes the captured admitted callback with agent-build readiness forced to
fail. Production `_run_after_agent_ready`, `_emit_terminal_turn_error`, `_emit`
and `write_json` produce a `message.complete` error (`agent_init_failed`) and
route it to that session's transport, not stdio. Transport identity is retained.
The model and thread scheduler are suppressed by the core owner fixture; this is
not a real WebSocket client, successful LLM turn, or packaged Desktop run.

The persisted conversation readback has role `user`, content and `_row_id`, so
the ordinary hydration data exists. **Immediate user-bubble hydration remains an
unimplemented/unverified seam**: admission persists `_submit_user_row` but emits
no canonical user-row event, and the decision plugin updates only its canonical
request/operation snapshot. Desktop's
`app/session/hooks/use-message-stream/gateway-event/message-stream.ts` handles
`message.start` by setting busy/turn flags, not inserting the external user row;
its delta handler appends assistant text. This is not proof that an already-open
pane renders the decision user bubble. Required follow-up: an owner-scoped,
UID/idempotency-aware external-user-row event or explicit authoritative transcript
refresh through the Desktop session-state seam, with duplicate/reconnect and
profile/pane isolation tests. Do not send a second prompt to manufacture a bubble.

Plan acceptance test 12 (actual sandbox packaged app decision → user bubble →
turn → ledger/card/message readback, no repeated execution) is still outstanding.
No Stage A action, commit, deploy, fence bypass or production-state mutation was
performed for these tests.
