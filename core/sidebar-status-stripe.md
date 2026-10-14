# Sidebar status stripe + gateway device glyph (Desktop)

Patches: `0038`, `0039` in `core/patches/`. Touches `apps/desktop` only. Replaces the per-gateway row stripe from
`0024` ([multi-gateway-projects.md](multi-gateway-projects.md)).

## Behavior

- The 3 px stripe on a session row's left edge shows the session's **status**, not its gateway:
  - **green**: the agent finished its turn and is waiting on you: dot state `unread`, or `idle` with at
    least one message. It stays after you open the row (`0039`; in `0038` it was unread-only).
  - **red**: not finished and needs help (dot state `needs-input`, i.e. blocked on a clarify/approval
    prompt, or `stalled`, i.e. a live turn that has gone quiet).
  - **grey**: you parked it with ⌘B (`0048`, [session-navigation-shortcuts.md](session-navigation-shortcuts.md)).
    Overrides every other colour; ⌘]/⌘[ and ⌘⇧]/⌘⇧[ skip it. ⌘B again clears it.
  - **none**: working, background/delegating, draft, archived, or a session with no turn yet.
- The owning gateway becomes a small device glyph right after the row's age: laptop, Mac mini or cloud
  (a generic monitor when nothing matches). Shown only when the registry has more than one connection.
  With the age column off, the glyph is the row's last trailing item. Like the age, it fades for the ⋯
  button on hover.

## How it works

- `sessionStatusStripe(state, hasTurns)` in `store/session-dot-state.ts` maps the row's resolved
  `SessionDotState` (the same one the status dot paints) to `'done' | 'stuck' | null`.
- `app/chat/sidebar/gateway-device-icon.tsx`: `gatewayDevice(connection, localHostname)` picks the kind.
  `cloud` connections → cloud. Otherwise a name is matched (`mini` → Mac mini, `macbook|laptop|book` →
  laptop): the connection's label + ssh host + id, or for the `local` connection the backend hostname from
  `/api/system/stats` (fetched once, cached in `$localHostname`). The Mac mini glyph is an inline SVG on the
  tabler 24 px stroke grid; laptop is tabler `IconDeviceLaptop` (exported as `Laptop` from `lib/icons.ts`).
- `sidebar/session-row.tsx` renders the stripe (`data-slot="session-status-stripe"`,
  `data-status-stripe="done|stuck"`) and puts `<GatewayDeviceIcon>` inside the age's tail span.

## Tests

`store/session-dot-state.test.ts` (`sessionStatusStripe`), `app/chat/sidebar/gateway-device-icon.test.ts`.

## Applying

Apply after `0024`. Name your gateways so the regexes match (e.g. a connection labelled `macbook`, or
an ssh host containing `mini`).

## Gotchas

- Gateway colors (filter menu) still exist and still color project headers, but no longer paint rows.
- An unmatched local hostname shows the monitor glyph; rename the host or extend the regexes.
