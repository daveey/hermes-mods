# Left-pane sidebar: what core owns

The left session list is core Desktop, not hermes-mods `desktop-plugins`. Read the installed source at `~/.hermes/hermes-agent/apps/desktop/src` rather than hunting for a checkout.

## Blocked section vs red stripe

The **Blocked** sidebar section and the red row stripe have different inputs and owners. The section is `apps/desktop/src/plugins/kanban/blocked-section.tsx` (`fetchBlockedTasks` in `api.ts`). It lists every blocked card on every gateway and board, grouped by `managing_session_id` (the latest `tui` notify subscriber), else by the card's `session_id`, titled via `/session-labels` on the backend's own profile. The stripe turns red only from a visible row's `kanban_blocked`. When the section shows a card but no row is red, check the card's owner session on its home gateway (`home_gateway`; the local replica can be empty, so read the owner's DB through `tailnet_exec`). Cards created by background profiles have sessions the user's chats never show.

## Row squares, ETA readout, Background counts

`apps/desktop/src/app/chat/sidebar/session-row-card-squares.tsx`: `COLOURS`, `sessionCardColours` (per-category counts from `kanban_*` session fields), `trailingColours` (flat rows drop blue and grey), `BackgroundCardSquares`, `SessionRowEtaReadout` (replaces squares only when the stripe is `external` and a card has a live `eta`; coloured sky-400).

- A wait category with no square of its own is counted as blue/grey and therefore vanishes from flat rows; with a stale/unknown source there is no ETA either, so the row shows nothing while the panel still draws the colour.
- Wait colours added in the mods panel/session-tasks chips (light-blue delivery, purple deploy) are not automatically in core. A new wait colour is finished only when core's squares, ETA readout and Background aggregate carry it too, including the stale/no-ETA last-known case. File that as a core card landed via `hermes-core-wt ship`.
