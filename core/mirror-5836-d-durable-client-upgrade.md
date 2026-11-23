# Durable client upgrade recovery across renderer reloads

Source patch ordinals: 5781–5783.

## Behavior

A `client_upgrade_required` refusal leaves a persistent client-install-wide recovery notice. Reloading the renderer, clearing unrelated notifications or opening Updates does not acknowledge compatibility. Explicit dismissal clears the reminder; another refusal restores it.

## How it works

`client-upgrade-recovery` stores only an acknowledgement bit under `hermes:client-install:upgrade-required-note`, not error payloads, sessions or credentials. Repeat refusals coalesce under `client-upgrade-required`. Native recovery opens the client updater rather than the selected remote backend; the hosted client offers reload. Notification actions can set `dismiss: false` so inspection/recovery is separate from acknowledgement.

## Code

- `apps/desktop/src/store/client-upgrade-recovery.ts`
- `apps/desktop/src/store/notifications.ts`
- `apps/desktop/src/components/notifications.tsx`
- `website/docs/developer-guide/macos-bundle-updates.md`

## Tests

Source test coverage only. **Not executed during this mirror pass.**

`apps/desktop/src/store/client-upgrade-recovery.test.ts` and `apps/desktop/src/components/client-upgrade-recovery.test.tsx` cover storage rehydration, coalescing, explicit dismissal, rendered primary/secondary actions, hosted reload and unavailable storage. `tests/scripts/desktop_update/test_post_commit_bundle_failure.py` covers preserving advanced code and the old complete bundle on a deferred swap failure. Archive reconnect coverage also exercises refusal recovery.

## Applying

Apply store, notification action and rendered-consumer changes together. Rebuild the client normally; no automatic update application or replay of the refused edit is introduced.

These ordinals are part of an ordered core mirror, not a claim of independently applicable patches. Use the repository's snapshot replay recipe and dependency order.

## Gotchas

Opening Updates or reloading is not proof the refused edit is now compatible. Storage failure still allows an in-window recovery affordance but cannot guarantee persistence. The notice is installation-wide, not a verdict about the active gateway.

## plugin?

No. This changes core behavior and its source tests; a separately loaded plugin cannot replace these internal boundaries.
