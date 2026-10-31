# Sidebar history, outcome parity and session-control owner recovery

Patches: 1337, 1347, 1359.

## Behavior

- 1337 distinguishes a released transcript from an unsent draft, even when the chat has no loaded recents row. A row with positive `message_count` overrides a draft claim; genuinely empty or unknown-count rows retain that claim. Background work still takes precedence.
- 1347 gives project-tree preview rows the same stored `outcome` and `muted` inputs as session-list rows. The regression checks unresolved, done and muted chats across both HTTP surfaces.
- 1359 resolves a resumed chat's control-read owner from its project-tree row when the chat is outside the recents page. An unresolved read fails closed and logs the miss; when an owner row arrives, it reads again and clears the error without using the ambient gateway.

## How it works

Transcript release records `transcriptReleased: true` when discarding retained messages. Draft detection excludes that state. `withRowActivity` changes a draft to idle when the row itself proves prior messages, then applies background/unseen activity as before.

`_project_tree_row` copies the outcome and boolean mute already present on enriched session-list rows, rather than silently dropping them during tree projection.

`ownerLookupSessionRows` searches recents, cron, messaging and project-tree rows before unlisted owner stubs. It includes both project previews and nested repository-group sessions, memoizing the merged array by input identities.

After a control-read owner miss, owner-bearing atom changes and a local-only two-second poll re-check resolution, bounded to fifteen minutes. No RPC is sent until resolution succeeds. A newer read/action/event, session disappearance or explicit clear supersedes the pending retry. Action owner misses are logged but do not arm this read-recovery mechanism.

## Code

- `apps/desktop/src/app/types.ts`.
- `apps/desktop/src/store/{session-states,session-dot-state}.ts`.
- `tui_gateway/methods_projects.py::_project_tree_row`.
- `apps/desktop/src/store/session.ts::ownerLookupSessionRows`.
- `apps/desktop/src/store/session-control.ts`.

## Tests

The following coverage is present in the commit diffs; tests were not run for this documentation task.

- `apps/desktop/src/store/session-dot-state.test.ts`: `a released transcript is not a draft, even with no listed row`; `a draft claim yields to a row that has messages`. Controls retain real drafts and preserve background status.
- `apps/desktop/src/store/session-states.test.ts`: `releaseSessionTranscript` expectations now include `transcriptReleased: true` for a legacy retained state; existing tab-accent and lightweight-state expectations are adjusted too.
- `tests/hermes_cli/test_session_outcome_surfaces.py::test_project_tree_rows_carry_the_list_rows_outcome_and_mute`: compares stripe inputs from `/api/profiles/sessions` and `/api/profiles/projects/tree` for unresolved, done and muted chats.
- `apps/desktop/src/store/session-control-owner.test.ts`: `routes the read by the project-tree row when the chat is outside the loaded recents page`; `fails closed and logs while the owner is unknown, then re-reads once the owner row lands`; `keeps failing closed while the owner stays unresolvable`.

## Applying

Apply 1337, 1347 and 1359 in their ordered fork-series positions with earlier session-state/owner-routing dependencies. Tree projection needs the backend update; draft classification and owner recovery need the updated Desktop renderer. No new configuration or hotkeys.

## Gotchas

The HTTP parity test establishes equal stripe inputs, not a mounted sidebar refresh animation. The owner tests cover a local-primary tree preview and later recents-row arrival; nested repository owners, remote-owner recovery, fifteen-minute expiry, supersession cleanup and action-miss logging are untested by these added regressions. Unknown ownership never authorizes an ambient fallback.

## Plugin side

None. These fixes live in the core sidebar state, project-tree projection and session-control routing.
