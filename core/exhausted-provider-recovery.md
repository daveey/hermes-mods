# Exhausted-provider recovery (not first-run setup)

Patch **0528** (source `42b8d9b755`; public series position 0528 after the 2026-10-27 reconciliation).

## Behavior

- When a chat or the runtime readiness check fails because every credential of the configured provider is
  exhausted (HTTP 402/429, billing or quota), the failure carries structured metadata instead of
  prose: `failure_kind: "exhausted"`, `provider` (string) and `reset_at` (UNIX **seconds**, `null` when no
  reset time is known). A provider with no usable credential at all reports `failure_kind: "auth_missing"` with
  the same `provider` / `reset_at: null`.
- Desktop shows the exhausted-recovery screen (`components/onboarding/exhausted.tsx`) instead of the first-run
  provider picker: it names the provider, shows the real reset time (never an invented one when `reset_at` is
  `null`) and opens the existing chat model menu. It still shows after the user skipped onboarding, and it does not
  delete the configured-provider cache.
- Background sessions and sessions owned by another connection/profile never open recovery; a deferred warning is
  applied only to the connection, profile and chat it came from.
- When both a `runtime_check` and a setup signal exist, `runtime_check` wins; readiness is `unavailable`
  (not `needs_setup`) for an exhausted provider.
- Legacy payloads (no `failure_kind`, auth wording only) keep the old behavior.

## How it works

`tui_gateway/credential_failure.py` reads the credential pool state only (never selects, refreshes or clears a
cooldown). `credential_failure_fields(provider)` returns `auth_missing` unless every live (non-dead) pooled entry is
`exhausted` for a 402/429/rate_limit/billing/quota reason with an unexpired window; then it is `exhausted` and
`reset_at` is the earliest window end. `exception_failure_fields` only annotates credential errors (`AuthError`,
`ProviderCredentialsExhaustedError`, `ProviderNotConfiguredError`; a `ProviderFallbackError` is unwrapped to the
primary's failure), so unrelated errors stay unrelated. `session_failure_payload` adds the fields at the common
`error` / `message.complete` emit boundary, inside the session's profile runtime scope. The fields are declared on the
gateway contracts (`contracts/events.py`, `common.py`, `config_free_tier_control.py`) and regenerated into
`apps/shared/src/gateway-contract.*`. The renderer parses them with `credentialFailureMetadata`
(`lib/provider-setup-errors.ts`), threads them through `runtime-readiness.ts` and `store/onboarding.ts`, and
`use-prompt-actions/submit.ts` routes a requested recovery to the onboarding store.

## Code

- `tui_gateway/credential_failure.py` (new), `tui_gateway/server.py`, `methods_config.py`, `model_switch.py`,
  `session_auto_continue.py`, `tui_gateway/contracts/{common,events,config_free_tier_control}.py`
- `apps/desktop/src/components/onboarding/{exhausted.tsx,index.tsx}`, `store/onboarding.ts`,
  `lib/{runtime-readiness,provider-setup-errors}.ts`, `app/session/hooks/use-message-stream/gateway-event/{status,session-info}.ts`
- Nine locales: `en/de/es/fr/ja/ru/zh_onboarding.ts`, `ar_boot.ts`, `zh-hant_boot.ts`, `types_onboarding.ts`; key index `locales/_keys.desktop.json`

## Tests

- `tests/tui_gateway/test_credential_failure.py`: exhausted vs auth_missing vs unrelated errors, nullable `reset_at`, wire contract (Python / RPC path).
- `components/onboarding/exhausted.test.tsx`: provider name, real UNIX reset time, no invented time, owner-scoped account/manual actions, recovery shown instead of the picker including after skip (Desktop renderer path).
- `store/onboarding.test.ts`, `lib/runtime-readiness.test.ts`, `gateway-event/status.test.ts`, `use-session-actions/utils.test.ts`, `skip-gate.test.tsx`: metadata survives unconfigured setup, runtime_check precedence, background/foreign-owner warnings ignored.
- Not covered by a test: the packaged Desktop bundle and a live exhausted provider end to end (live acceptance is tracked by the requesting card, not this doc).

## Applying

`git am -3 core/patches/0528-*.patch` after `0527`. Run `bash scripts/run-in-hermes-env python3 scripts/gen_gateway_contracts.py` if contracts drift, and `npm run i18n:keys`.

## Gotchas

- `reset_at` is seconds, not milliseconds; format with `* 1000` in the renderer only.
- Every new key needs all nine locales (en, de, es, fr, ja, ru, zh, ar, zh-hant) plus the `_keys.desktop.json` index.
- Diagnosing must stay read-only: never clear a cooldown to make the screen go away.

## Plugin?

No: the readiness check, gateway contracts and onboarding store are core code with no plugin surface.
