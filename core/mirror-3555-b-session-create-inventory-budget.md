# Session create/resume reply bounded on the banner inventory

Patches: **3546**.

## Behavior
- `session.create` and resume replies wait at most 2 s for the tool/skills/MCP inventory. Past that, the reply carries a lazy shape (empty tools and skills, no MCP servers, `inventory_pending: true`); when the build finishes, the full info is published as a `session.info` event with `inventory_pending: false`.
- Oneshot and tool-source sessions skip the inventory entirely.
- Concurrent opens of one session share one in-flight build.
- The explicit `session.info` RPC still returns the full inventory without a bound (untested here).
- The classic CLI shows the compact banner while the inventory is pending.

## How it works
- 3546 adds `reply_info` in the session-local inventory module (used by resume, hence create) with `REPLY_INVENTORY_BUDGET_S`, merges the late result with a fresh local session info so a turn started meanwhile is not reported idle, adds `inventory_pending` to the `SessionInventoryFields` contract (regenerated TypeScript and OpenRPC), and makes `banner_inventory()` return None while pending.

## Code
- `gateway/session_local_inventory.py`
- `gateway/session_controls.py`
- `hermes_cli/gateway_session_store.py`
- `tui_gateway/contracts/canonical.py`
- `apps/shared/src/gateway-contract.generated.ts`
- `apps/shared/src/gateway-contract.openrpc.json`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

- `tests/gateway/test_session_create_inventory_budget.py`: `test_create_reply_does_not_wait_on_a_60s_inventory_and_info_follows`, `test_non_interactive_sources_never_build_the_inventory`, `test_inventory_ready_within_budget_rides_the_reply_without_an_event`, `test_concurrent_opens_of_one_session_share_one_build`.
- `tests/hermes_cli/test_gateway_session_store.py`: `test_banner_inventory_is_none_while_the_gateway_still_builds_it`.
- `tests/gateway/test_canonical_session_inventory.py`: existing tests adjusted (no new test names).

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
- Clients must handle `inventory_pending` and the follow-up `session.info` event; the Ink renderer reportedly already does (untested here).
