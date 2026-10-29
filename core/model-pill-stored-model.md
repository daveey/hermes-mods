# Composer model pill and effort menu use the chat's stored model

Patches **0473**, **0484**, **0495** (sources `954bfad790`, `3232b40b24`, `3454645daa`).

## Behavior

- A resumed chat with no live agent shows its stored model in the composer pill (with a pulse and a "loading this
  chat's model" tooltip) instead of a bare spinner; the bare spinner appears only for a draft, and only for 5 s, then
  the pill names itself as the picker. Unknown models are named, not blanked (0484).
- The effort/reasoning menu follows the chat's stored model/provider instead of the profile default, so a Claude chat no
  longer offers GPT's Thinking/Fast/Ultrafast. A stored model without a provider is resolved from the catalog row that
  lists it.
- Lazy resume preserves the stored model without building the agent (0484).
- 0495 regenerates `locales/_keys.desktop.json` for the pill's loading key; without it `i18n-keys.test.mjs` is red on main.

## How it works

Session list rows project the stored route's provider (`model_provider`, from `SessionDB.session_gateway_runtime`)
before `model_config` is stripped. `useStoredModelSelection(view)` (`app/chat/stored-model.ts`) reads the stored
model; `ModelPill` and `use-model-menu-controller.ts` consume it.

## Code

`apps/desktop/src/app/chat/composer/model-pill.tsx`, `app/chat/stored-model.ts`, `app/shell/use-model-menu-controller.ts`,
`lib/model-status-label*`, all locale files, `tui_gateway/methods_session.py`, `locales/_keys.desktop.json`.

## Tests

`model-pill.test.tsx` (stored label with tooltip; never spins for an unloaded stored chat), `reasoning-menu-panel.test.tsx`
(prefers the chat's model), `tests/tui_gateway/test_session_resume_materializes_minted_key.py` (lazy resume keeps the
stored model; missing key still 4007), plus the backend tests for fallback session info and list-row provider
projection added in 0473.

## Applying

`git am -3 core/patches/0473-*.patch`, `0484`, `0495` in order; run `npm run i18n:keys`.

## Gotchas

Translate the new key in every locale overlay or list it in `overlay-gaps.json`.

## Plugin?

No: composer and resume are core renderer/gateway code.
