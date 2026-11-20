# ⌘B mute for older drawn sidebar rows

Mirror entries 5037, 5038 and 5039.

## Behavior

⌘B on sidebar chats **outside** the ~50-row live `$sessions` list now POSTs `/mute` using a bounded drawn-row registry.

- Acked writes hold through the original 10s stale-page window, then drop on agreement; 10 min is eventual expiry.
- Drawn-only rows that already agree when the ack lands are reconciled at the 10s deadline via a cancellable hook timer.
- OFF-list / undrawn rows: untested beyond the drawn-registry tests below.

## How it works

`session-park-rows.ts` remembers drawn rows. `session-blocked.ts` keeps mute stale-page protection after ack. `use-session-parked.ts` reconciles stable mute agreement at the guard deadline.

## Code

- `apps/desktop/src/store/session-park-rows.ts`
- `apps/desktop/src/store/session-blocked.ts`
- `apps/desktop/src/lib/use-session-parked.ts`
- `apps/desktop/vitest.setup.ts`

## Tests

Source tests (not executed here): `apps/desktop/src/store/session-blocked.test.ts`, `apps/desktop/src/lib/use-session-parked.test.ts`.

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); never apply these mails on their own.

## Gotchas

- The live `$sessions` list is not the full sidebar: older drawn rows need the registry or ⌘B is a no-op.
- Agreement at ack time still waits for the 10s guard timer before dropping protection.
