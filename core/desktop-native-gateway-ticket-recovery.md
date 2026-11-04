# Desktop: native gateway dials recover after a ticket refusal

Patches: **1719** (`881b67a021`, `fix(desktop): recover native gateway dials after ticket factory refusal [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- A native gateway dial whose ticket factory refused once reconnects on the next attempt instead of staying disconnected.

## How it works

`apps/shared/src/json-rpc-gateway.ts` clears the refused state so the reconnect loop asks for a fresh ticket.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1719: (subject only)

## Code

`apps/shared/src/json-rpc-gateway.ts`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/src/store/gateway-local-ticket-reconnect.test.ts`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
