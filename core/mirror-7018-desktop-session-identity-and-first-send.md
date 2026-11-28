# Desktop session identity across gateway renames, clarify attention and first send

Patch coverage: 6646, 6647, 6648, 6649, 6650, 6961, 6962, 7017.

## Behavior

- **Clarify stays red.** An unanswered clarify keeps the session in needs-input until that request settles; unrelated, delayed or replayed tool completions no longer clear it.
- **Same session, one owner.** A session id seen through canonical gateways, or after a gateway-name change, resolves to one owner row per connection. Route-carry rows keep the original list connection; a carried route is transport provenance, not identity.
- **First send.** A first message to a new chat that meets "Chat model is still loading" retries the session-create RPC every 2 s for up to 180 s while the hub answers 400 "Chat model is still loading" instead of being dropped; if the deadline passes or the hub refuses otherwise, the first message is parked as a retry draft and the chat stays listed as "Not sent - retry" in the sidebar. Review fixes keep the drift baseline across retry attempts and avoid yanking the user back after a chat switch.
- **Background process label.** Linked background processes are labelled by the agent's command, not an injected scheduling preamble.

## How it works

`apps/desktop/electron/session-owner-identity.ts` and `src/lib/session-owner-identity.ts` (`canonicalGatewayOf`, `connectionOf`, `conflicts`, `ownerTagOf`, `sameOwnerRow`), `electron/profile-session-routing.ts` (`RowIdentitySet`), `gateway-event/tools.ts` (`releaseNeedsInputWhenSettled`), `use-prompt-actions/utils.ts` (`isChatModelLoadingError`), `use-composer-submit.ts` (`keepUnsentChatListed`), `draft-rows.tsx`, `session-index.ts`; backend `tools/process_display.py` (`display_command`).

## Code

`apps/desktop/electron/{session-owner-identity,profile-session-routing}.ts`, `apps/desktop/src/lib/session-owner-identity.ts`, `src/store/session.ts`, `src/store/prompts.ts`, composer/prompt-action hooks and sidebar draft rows listed above, `tools/process_display.py`, `hermes_cli/process_session_activity.py`.

## Tests

Read from source; **not executed here**; no full Electron E2E claim. `profile-session-routing.test.ts`, `clarify-needs-input-attention.test.tsx`, `use-composer-submit.restore-scoping.test.tsx`, `draft-rows.test.tsx`, `session-index.test.ts`, `store/session.test.ts`, `test_process_display.py`, `test_process_session_activity.py`. Real multi-gateway rename in a running app is **untested**.

## Applying

Apply in ordinal order; 6962 corrects 6961.

## Gotchas

- Do not compare a carried route as provenance when deciding ownership.
- The loading-retry must not wrap baseline capture.

## Why core, not a plugin?

Desktop renderer/main and core process labelling.
