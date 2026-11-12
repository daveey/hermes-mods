# Session writes ride out owner-discovery stalls; Desktop Retry

Patches: **2504, 2505**.

## Behavior

A Desktop archive PATCH was refused with a 503 `owner_unavailable: control_timeout ... Drain and stop the gateway` because the session-write route did one 1.5 s read-only owner identify while the owning gateway's control loop was briefly blocked, although the owner answered the next read. Now:

- Backend: a missed identify (`inaccessible` / `control_timeout`, no endpoint) is re-read, at most 3 attempts of up to 1.5 s with 0.25 s backoff, within one 6 s monotonic budget. Every other verdict is final on the first read (`absent` keeps the offline exclusive path; auth, protocol, permission and profile refusals are never retried). Discovery never starts a gateway, and the mutation is sent at most once and never retried server-side. An exhausted `control_timeout` is a retryable 503 saying nothing was changed, with no drain/stop advice.
- Desktop: archive and rename failures restore the row and show a Retry only while the mutation client still retains the intent, so Retry replays the same request id and expected revision. The archive owner is pinned once and Retry skips preflight and deselect. A selected-row owner rename now uses the fenced `renameSession` instead of an unfenced PATCH.
- Patch 2505 extracts the stored-session tile close into a shared helper (no behavior change).

## How it works

- New `hermes_cli/session_owner_discovery.py`: `discover_session_owner(home)` runs each read in a thread under `asyncio.wait_for` capped at the remaining budget, so an overrunning read cannot stretch the write past the budget; `is_transient` and `owner_unavailable_detail` choose the final 503 text. Used by `_mutate_via_gateway_owner` (`web_server_sessions.py`) and the router's `_on_live_owner` (`web_routers/sessions.py`).
- `apps/shared/src/session-http-mutations.ts` exports `isDefinitiveSessionMutationRefusal`; the mutation client drops the retained identity only for those refusals, so a 503 stall, timeout or lost reply keeps it.
- `apps/desktop/src/lib/session-write-retry.ts` `sessionWriteRetryAction` returns the toast Retry action or none; `api/sessions.ts` adds `pinSessionWriteOwner`; `use-session-actions/archive-commit.ts` `commitArchive` re-hides the row and resends on Retry; `session-actions-menu.tsx` applies the same Retry to rename.
- `close-stored-tile.ts` `closeStoredSessionTile` (2505) is a pure refactor shared by delete and archive.

## Code

Backend: `hermes_cli/session_owner_discovery.py` (new), `hermes_cli/web_routers/sessions.py`, `hermes_cli/web_server_sessions.py`. Desktop/shared: `apps/shared/src/session-http-mutations.ts`, `apps/desktop/src/api/sessions.ts`, `apps/desktop/src/lib/session-write-retry.ts` (new), `apps/desktop/src/app/session/hooks/use-session-actions/archive-commit.ts` (new), `.../use-session-actions/close-stored-tile.ts` (new), `.../use-session-actions/index.ts`, `apps/desktop/src/app/chat/sidebar/session-actions-menu.tsx`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/hermes_cli/test_session_owner_discovery_retry.py` (retry budget, final verdicts, single mutation send, exhausted-stall 503 text, a real stalled owner; entry paths: `_mutate_via_gateway_owner` and the router live-owner forward) and the updated `tests/hermes_cli/test_web_server_session_owner_forward.py`. Desktop: `api/session-write-retry.test.ts`, `session-actions-menu.test.ts(x)` (rename Retry), `use-session-actions/archive-commit.test.ts`, and the updated `use-session-actions.test.tsx` / `archive-linked-confirm.test.tsx`. The 2505 refactor has no test of its own beyond those suites. The root cause of the control-loop stall is not identified; other write kinds (delete, other PATCH fields) are untested.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- A refused write can now take about 6 s before it fails.
- A Retry is offered only for non-definitive failures; conflicts and not-found show no Retry.

## Plugin

Core change; no plugin required.
