# Cross-gateway capability routing: a wall opens an owner card where the capability lives

Patches: **0536 0537 0538 0539 0540** (feature, two review follow-ups, create-parity test, no-producer-no-park integration). Companion: hermes-hub client 1.7.2 (`capabilities` collection). Builds on [assignment capabilities](kanban-assignment-capabilities.md) (0300).

## Behavior

A worker that hits a wall another gateway could clear (a cloud account only one machine is signed in to, ssh reachability, an admin GitHub token, passwordless sudo) declares the need instead of parking on a probe nobody produces:

- `kanban_block(..., needs=["aws:<account>:<action>"])`, `kanban_schedule(..., needs=[...])`, CLI `hermes kanban block|schedule <id> --need <capability>`.
- A card can declare needs up front in `requires` (namespaced names next to `terminal`/`file`/...). Creation pre-routes them (placement preflight).

When this gateway lacks a need and another gateway holds it (fresh entry in the shared registry), ONE owner card is created on the holder through `hermes-hub create-task <gw> ... --id capability-owner:<card>:<need>`. Its body is the waiting card's scoped action, a readback step and the wake. If the action exceeds the approved plan, the owner card (not the worker) files the `human_request`. The waiting card parks `scheduled` with wake probe `hermes kanban wait-check capability-owner <request ids>` and wakes when the owner card is done.

Typed refusals, nothing written: `no_capability_holder` (points at `human_request`), `capability_registry_stale` (a holder exists but was not live-checked within `stale_after_seconds`; stale data never routes), `capability_held_locally` (the wall is the profile: use `kind='assignment'`), `capability_route_exhausted` (this card already routed that need; a rejected request may be re-routed once as `:g2`).

`hermes kanban capabilities list|refresh|locate <need>` shows the registry.

## How it works

- Registry (`kanban_capability_registry.py`): `kanban.capabilities.probes` maps names to shell probes (exit 0 = held); `ssh_hosts` adds BatchMode ssh probes; builtins add `sudo:nopasswd` (`sudo -k -n true`) and `github:<login>[:<scope>]` (`gh api user`, `gh auth status --active`). The dispatcher tick refreshes off-thread every `refresh_seconds` (600) and writes `<kanban home>/kanban/capabilities.json` with names, ok and `checked_at` only, never commands or output. Names are namespaced (`ns:part[:part]`); `<ns>:<x>:admin` covers `<ns>:<x>:*` (depth >= 3 only).
- Transport: hermes-hub >= 1.7.2 publishes the file as `capabilities/info`; every gateway's replica (`hub.db`) then knows who holds what. The server needs no change (collections are generic).
- Routing (`kanban_capability_routing.py`): decide from the replica, queue owner requests (exact resend of an already-queued envelope, so retries after a partial failure are idempotent), then park via `kanban_timed_park.park` in one transaction and append a `capability_routed` event whose `awaiting` names the owner request as the producer. Preflight parks only a still-unclaimed `ready` card. Owner bodies are redacted.
- Profile validation (`kanban_capabilities.py`) passes namespaced needs through untouched; they are card needs, not profile tools.

## Code

- `hermes_cli/kanban_capability_registry.py`, `hermes_cli/kanban_capability_routing.py` (new).
- `tools/kanban_tools.py` (`_route_needs`, create preflight), `tools/kanban_tools_schemas.py` (`needs`, `requires` pattern).
- `hermes_cli/kanban.py` (`--need`, `capabilities`, create preflight), `kanban_parser.py`, `kanban_wait_check.py` (`capability-owner`), `kanban_continuation.py` (refresh hook), `kanban_capabilities.py`.

## Tests

`tests/hermes_cli/test_kanban_capability_routing.py` (31 cases), run with the repo test environment:

- Replay of a real block (cloud describe denied on one gateway, admin held on another) through the `kanban_block` tool: one owner request to the holder with the exact id, the card parks on `wait-check capability-owner`, and the probe reads pending -> running -> done (exit 1, 1, 0).
- Typed refusals: no holder (points at `human_request`), stale registry (no request, card stays running), not-held entry, held locally, needs + human_request.
- Route exhausted on a second route; one re-route after a rejected request; rejected/unknown request ids are "cannot tell" (2), never "not yet".
- Partial failure then retry resends identical envelopes; preflight does not park a card claimed during the hub call; owner body redaction; collision-free request ids; `admin` depth rule.
- Placement preflight through `kanban_create` (pre-routed and parked; no holder -> created with a warning); CLI `block --need` and `wait-check capability-owner`.
- Registry refresh runs probes and stores metadata only; cross-repo contract: the hermes-hub client's `collect_capabilities` record is what routing reads.

hermes-hub: `client/tests/test_capabilities.py` (metadata only, no tombstone on unreadable registry).

## Applying

Apply after 0300 (shared `requires` validation). Needs hermes-hub client >= 1.7.2 on every gateway and the same `kanban.capabilities.probes` everywhere (a shared config: each gateway live-checks, only the one where a probe succeeds holds it).

## Gotchas

- Only explicit `needs`/`requires` route; reason prose is never parsed into needs.
- The owner card is created untracked (`--origin-session ''`): tracked creation needs a creator session on the destination. The waiting card's park is what its origin chat sees.
- A probe proves the credential works, not its exact privileges: name capabilities after what the probe actually shows.
- Gateways whose hermes-hub client predates 1.7.2 publish nothing, so they are never holders.

## Plugin relationship

Core: the block/schedule transitions, create preflight and the dispatcher tick are core transactions. The transport half lives in the hermes-hub client.

## Follow-up patches (0561, 0567)

**0561** is a lint fix (ruff FURB167/RUF100 and Windows-footgun rules) in `kanban_awaiting.py`, `kanban_capability_registry.py`, `kanban_capability_routing.py` and `tests/cron/test_cron_model_provider_pairing.py`; no behavior change. **0567** adds `hub_cli_path()` (configured value, then `PATH`, then `~/.local/bin`) and routing's `_hub_cli` uses it; see [kanban-default-placement.md](kanban-default-placement.md).

**0593** (source `a2c2b3eba8`), test-only: the capability-routing fixture drops PATH entries that contain a real `hermes-hub`, so `hub_cli_path()` assertions do not depend on the host having the client installed (`test_replay_t5bc8e125_block_opens_macbook_owner_card_and_parks`). The same commit lets the real-home guard read the checkout's sibling `manifest.json`; that exemption is covered in [kanban-origin-ownership-snapshot.md](kanban-origin-ownership-snapshot.md).

## Follow-up patch (0686)

**0686** (`41a1d7ce4d`): `owner_status()` used to read the owner card id only from `result['task']`. An applied create that came back as `{"output": <hermes kanban create --json text>}` (clipped, so often not valid JSON) made the wait-check exit 2 `done without a task id` and wake the waiting card even though the owner card was done.

`owner_task_id` reads, in order: `task`, `task_id`, `id`, then `output`'s JSON `id` / `task_id`, then the first `"id": "t_…"` in the output text. rc 2 only when none of those holds an id.

Tests in `tests/hermes_cli/test_kanban_capability_routing.py`:

- `test_owner_status_reads_the_owner_id_from_every_applier_shape`
- `test_owner_status_without_any_id_is_cannot_tell`
- `test_woken_card_needing_another_gateway_is_claimed_here_once_its_owner_is_done`

This mirror did not re-run that file. A live hermes-hub applier returning a clipped `output` is untested; the tests feed the shapes directly.
