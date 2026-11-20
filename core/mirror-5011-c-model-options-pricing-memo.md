# model.options memo follows asynchronous pricing

Patches: **4594–4595** (5011 snapshot).

## Behavior

`model.options` payload memos stamp pricing publication generation, provider endpoint-cache selection, expired pricing deadlines, and cached entitlement. Reader eviction of expired prices advances generation under the catalog lock so an empty expired-key tuple cannot restore a pre-expiry memo.

## How it works

`models_pricing` exposes `pricing_cache_version`. Inventory memo keys include that stamp. Normal pricing cleanup, memo TTL, and read-only worker controls are unchanged.

## Code

- `hermes_cli/inventory.py`
- `hermes_cli/models_pricing.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_model_options_memo.py`
- `tests/hermes_cli/test_model_options_pricing_invalidation.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Endpoint-cache-only changes still invalidate without catalog mutation. Cover both another consumer evicting first and the picker evicting itself. Hub A/B timings in source messages are not this pass's results.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
