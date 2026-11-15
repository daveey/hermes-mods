# Quota-wall fallback for resumed provider-native routes

Patches: 3803, 3828.

## Behavior

A resumed Desktop row carrying its provider's native endpoint and stored wire mode can still move off a far quota wall. Explicit API keys and foreign endpoint overrides remain refusal boundaries; builds without a session identity keep the wall quietly.

## How it works

`tui_gateway.agent_factory` uses `overrides_endpoint` instead of treating any stored override value as foreign. On a permitted `walled_pin_deferral`, it clears the old overrides so the fallback does not inherit the walled provider's endpoint/wire. Refusal logging distinguishes key and endpoint cases.

The native-endpoint fixture checks the constructed agent's fallback model/provider and routing provenance, not just a returned config. The later sessionless fixture checks no fallback mutation or warning is emitted for absent identities, including repeat probes.

## Code

- `tui_gateway/agent_factory.py`

## Tests

**Tests were not rerun by this documentation pass.** The assertions below describe the staged test diffs, not fresh execution results. Full replay verification is owned by the parent mirror pass; no deployment or live-runtime claim is made.

- `tests/agent/test_walled_default_pin.py` (patches 3803, 3828).

- **3803:** A resumed provider-native URL/wire builds the fallback agent, includes the fallback notice and provenance, and does not carry the old endpoint/wire; explicit endpoint/key controls remain refusal cases.
- **3828:** Sessionless build cases preserve the quota refusal and selected route without not-deferred warnings, including repeated calls.

## Applying

Use the complete ordered series and the [README Applying recipe](README.md#applying), including its historical integration bridges and ancestry-only handling where listed. Then run the mandatory snapshot-bound [hostname escape repair](hostname-escape-repair.md) and repaired-tree checks specified there. These ordinals are documentation groupings, **not independent cherry-picks**; do not apply just this subset or a one-glob series that omits bridges. The parent mirror pass must establish replay/tree verification for the final snapshot.

## Gotchas

The fallback is session-scoped, not an automatic global route rewrite. An own key is still an explicit override even on the provider-native URL. Sessionless inventory/build probes must not change another session's selected model.

## Plugin?

No. This is part of core session construction and managed route/wall policy.
