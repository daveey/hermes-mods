# Provider overlay never overwrites a configured default model

Patch **0470** (source `7dcc9a1d61`).

## Behavior

The Desktop provider overlay opens on its own when a credential fails (`requestDesktopOnboarding()`, `manual: false`).
It used to treat every non-manual completion as a first run: after "I've signed in" it wrote the provider's
recommended default to `config.yaml` BEFORE the confirm card. On 2026-10-26, with ChatGPT quota exhausted, that flipped
`gpt-6-astra` / `openai-codex` to `claude-sonnet-5` / `anthropic` three times, and dismissing the card kept the flip.

Now only a genuine first run pre-persists (no `model.default` + `model.provider` in config, `configured` not true,
not manual). On any configured install every global write waits for the confirm card:

| Action on the card | Write |
|---|---|
| shown (OAuth, PKCE, device code, external recheck, API key) | none |
| Change (model picker) | none, the card only updates |
| Begin | exactly one `POST /api/model/set` with the model on the card |
| dismiss / cancel | none |
| local / custom endpoint | none until Begin, which writes `custom` + base_url + api_key + model together |

The card shows "Begin sets <model> as the default model for new chats." when the write is deferred.

## How it works

- `apps/desktop/src/store/onboarding.ts`
  - `fetchProviderDefaultModel` also returns `mainModelConfigured` (options carry both `model` and `provider`).
  - `shouldDeferGlobalDefault()` = `manual || configured === true || mainModelConfigured`.
  - `completeWithModelConfirm` pre-persists only when not deferring, and marks the confirming flow
    `deferPersist: true` otherwise. The runtime gate still validates the just-connected provider through
    `requestedProvider`.
  - `setOnboardingModel` skips the write when `flow.deferPersist`.
  - `confirmOnboardingModel` writes on Begin when `manual || flow.deferPersist`, with the flow scope, and with
    `pendingEndpoint` for a local endpoint.
  - `saveOnboardingLocalEndpoint` parks the probed endpoint in `pendingEndpoint` on a configured install and skips the
    runtime gate (the custom provider isn't in config yet; the probe already proved it answers).
- `apps/desktop/src/components/onboarding/flow.tsx` renders the note; `onboarding.defaultChangeNote` is in every
  locale (`ar_boot`, `zh-hant_boot` for the sparse ones), keys regenerated with `npm run i18n:keys`.

## Tests

- `apps/desktop/src/store/onboarding.test.ts`, `describe('credential-failure overlay on a configured install ...')`:
  zero writes before the card then exactly one on Begin; unknown cached flag but configured main model; dismiss writes
  nothing; Change pick writes only on Begin; local endpoint deferred; true first run still pre-persists (control).
  The first five fail on the base commit, the control passes on both.
- `onboarding-owner.test.ts` (manual flows): Change pick and local endpoint now write once, on Begin, still pinned to
  the initiating owner.
- `components/onboarding/flow.test.tsx`: note shown with `deferPersist`, absent on a first run.

## Applying

`git am -3` 0470. Touches only the onboarding store, the confirm panel and the onboarding locale blocks.

## Gotchas

- Manual flows changed too: a Change pick on the card used to persist at once, so a dismissed card still left that
  model behind. It is now Begin-only.
- A fresh install whose config already names a model and provider counts as configured, so its first sign-in also
  waits for Begin. The confirm card is always shown in that case, so nothing is lost.
- Why the overlay opens for a temporarily exhausted provider is a separate follow-up.
