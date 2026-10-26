# Account-gated Codex models route through the ChatGPT/Codex subscription

Patches:

- `patches/0236-fix-models-Astra-routes-via-Codex-subscription-catal.patch`: catalog spans all pool entries, plan-gated lease, metered rows labeled.
- `patches/0237-fix-models-honor-explicit-billed-picks-and-guard-sub.patch`: explicit billed picks honored; subscription defaults guarded end to end.
- `patches/0238-fix-auth-carry-model-entitlement-through-every-pool-.patch`: entitlement carried through every credential-pool retry branch.
- `patches/0239-test-models-exercise-subscription-picker-through-gat.patch`: picker exercised through the gateway RPC harness.
- `patches/0240-refactor-auth-isolate-Codex-entitlement-policy-and-k.patch`: policy moved to `agent/codex_entitlements.py`.

## Behavior

An account-gated Codex model (the fixture calls it `gpt-6-astra`) must run on a ChatGPT/Codex subscription whose plan is entitled,
never silently on the metered OpenAI API and never on a free-plan pool entry.

- Model discovery unions the live catalogs of every usable Codex credential (strongest plan first), so one free-plan entry at the
  round-robin cursor no longer hides the model.
- The picker lists subscription rows ahead of metered OpenAI-API rows and labels the billing. An unqualified model choice
  prefers the subscription when the account's discovered catalog has that exact model; an explicit provider choice is honored
  (a deliberately billed pick stays billed).
- Credential selection for a gated model skips entries whose plan claim is positively non-entitled (`free`, `go`, `guest`);
  a missing or unknown claim is not excluded. Retries, rotation and pool exhaustion never fall through to a non-entitled entry
  or the free singleton credential, and the rotation of an unknown failed identity never lands on a free entry.
- A session created with a metered pick of a model the subscription also serves is re-routed to the subscription unless billing was chosen explicitly.

## How it works

`agent/codex_entitlements.py` (0240) holds `codex_plan_type(token)` (the `chatgpt_plan_type` JWT claim) and
`codex_entry_entitled_for_model`. `hermes_cli/codex_models.py` builds `codex_catalog_credentials()` and the union catalog,
with a cache key from the credential set (not the cursor). `agent/credential_pool.py` applies the entitlement check at
selection and in `_rotate_unmatched`; `hermes_cli/models.py`/`inventory.py` mark rows (`SUBSCRIPTION_PROVIDERS`,
`METERED_OPENAI_PROVIDERS`, `_prefer_subscription_rows`); `runtime_provider.py` and `tui_gateway/methods_session_model_guard.py`
re-route and guard the final token.

## Code

- `agent/codex_entitlements.py`, `agent/credential_pool.py`, `hermes_cli/codex_models.py`, `hermes_cli/models.py`, `hermes_cli/models_detect.py`, `hermes_cli/inventory.py`, `hermes_cli/runtime_provider.py`, `tui_gateway/methods_session_model_guard.py`.

## Tests

- `tests/hermes_cli/test_codex_subscription_routing.py`: plan claim and entitlement, pool selection skips free entries, union catalog, cache identity ignores the cursor, picker defaults/labels, session-create re-route and explicit-provider honoring, runtime rejects a free fallback, Astra retry never rotates to free (incl. unknown failed identity).
- `tests/tui_gateway/test_codex_subscription_picker.py`: the picker through the gateway RPC harness (`model.options`).
- Not re-run when this doc was written (untested here).

## Applying

`git am -3 --empty=keep` in order 0236-0240. 0240 assumes the helpers 0236 first put inside `credential_pool.py`/`codex_models.py`.

## Gotchas

- The tier list is a deny-list of known free tiers; a new free-tier name would not be excluded until added.
- Union discovery keeps each token bound to its own host (base_url) and is read-only: it never refreshes or rotates credentials.

## Plugin?

No.
