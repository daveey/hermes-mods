# Cross-gateway card creation keeps the caller's identity and distinctness

Patches **2140, 2141**.

## Behavior

When `kanban_create` places a card on another gateway through the `hermes-hub create-task` courier:

- The caller's `idempotency_key`, `distinct_from` and `distinct_reason` (`IDENTITY_OPTIONS`) are sent to the receiver verbatim instead of being dropped. `distinct_from` must be 1 to 32 task ids; `distinct_reason` a non-blank string up to 500 chars; the two are required together. Invalid values raise `create_option_invalid: <key>` and refuse placement with no local fallback.
- An older hub client without `--option` support cannot carry them: if any identity option is present the call returns a fatal "create identity requires hermes-hub create-task --option" rather than silently creating a lossy or local duplicate (they are in `LEGACY_UNCARRIED`).
- With an explicit key, a previously queued/sent/done request is no longer short-circuited locally; the courier is asked again so the full envelope can be compared. A `request_id_conflict` from the hub is fatal, and `_place_remote` refuses instead of creating a second local card. The returned note says changed calls with an explicit key are refused.
- 2141 separates transport from creation identity: the placement forwarder's crash-retry key (`placement-forward:<gateway>:<task>`) is passed as `transport_key`, so it shapes the request id but is not sent as the receiver-side creation key.

## How it works

`_identity_options` validates; `carried_options` starts from it and skips identity keys in the generic loop. `create_remote` computes `request_id` over `transport_args` (args with the transport key substituted) and the options, and returns `fatal` / `retry` fields. `kanban_placement_forward.remote_args` no longer sets `idempotency_key`; `Forwarder` passes `transport_key`. `tools/kanban_tools._place_remote` treats `placed["fatal"]` like an explicit foreign pin.

## Code

- `hermes_cli/kanban_placement.py`, `hermes_cli/kanban_placement_forward.py`, `tools/kanban_tools.py`.

## Tests

**Source regression coverage; not executed in this mirror documentation pass.** Names come from the source diff; no pass/fail result is claimed.

`tests/hermes_cli/test_kanban_placement_identity.py` (new) and edits to `tests/hermes_cli/test_kanban_placement.py`: `test_carried_identity_is_exact_json_data`, `test_invalid_identity_is_not_coerced_or_dropped`, `test_legacy_client_never_drops_identity`, `test_full_tool_envelope_retains_identity`, `test_explicit_key_retry_revalidates_envelope_without_new_identity`, `test_direct_create_refuses_legacy_safety_metadata`, `test_distinct_scope_participates_in_implicit_request_identity`, `test_invalid_identity_refuses_auto_placement_without_local_fallback`, `test_explicit_key_offline_retry_keeps_prior_outcome`, `test_legacy_auto_create_keeps_explicit_key_locally`. Patch 2141 replaces the earlier `test_legacy_forwarder_falls_back_without_sending_a_lossy_request` with `test_forwarder_transport_identity_survives_pre_receipt_crash`, parameterized for modern and legacy clients.

## Applying

Apply ordinals 2140 and 2141 together, as part of the snapshot-aware full replay described in the [README](README.md) (full recipe in its Applying section), in published order, not as independent patches. 2141 amends 2140's forwarder wiring. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

- The public replay of `kanban_placement.py` carries a preexisting scrub artifact in its routing patterns, a regex written as `r"\mini-1\b"` (present since ordinal 0571). It is unrelated to this feature and intentionally not fixed here. Full-tree equivalence under the historical normalization policy is not a claim that this inherited malformed pattern works at runtime.
- An explicit key that names changed metadata is refused by the receiver; callers must pick a new key for a genuinely different card.
- Content-addressed retries (no explicit key) still short-circuit locally.

## Could this be a plugin?

No. It changes the core `kanban_create` tool and the placement courier contract with the hub.
