# Session Panel

The panel displays the current chat's tasks and decisions using the owner-qualified session-tasks and user-steps projections.

## Blocked cards and retired decisions

A canonical decision can be dismissed, withdrawn, or resolved while its owner card still reports `blocked`. Deduplicating the decision must not make that card undiscoverable.

- A request represented in the decision queue continues to own its presentation; no duplicate ask is added.
- Otherwise, a fresh blocked card appears under **Waiting**, with its gateway/profile/board/card identity, current state, and previous request marked **reference only**.
- **Open card** uses the original qualified chip. This status row never submits a decision, drafts a message, or reopens a retired ask.
- Stale rows remain status-unknown; terminal and released cards are not resurrected.
- Queue membership and panel visibility use the same predicate, including pending receipt readback. Receipt-only work keeps the queue mounted even when no actionable asks remain.

The bridge additions `presentationForSource(source)` and `hasQueuePresentation()` are optional for compatibility with older user-steps installations. They are scoped to the focused connection/profile/session; matching uses existing exact-source provenance rules.

## Finished history

Open cards remain visible by default. Done and archived cards share a collapsed **N finished** button; expanding it mounts their rows. Expansion is held only in renderer state, keyed by connection/profile/chat, and resets on remount. A finished-only chat retains the disclosure. Existing Done badges still count only done cards; needs-you and live agent projections are unchanged.

The display-only `/session-cards` feed includes local done/archived cards and settled remote receipt history from the core's gateway/profile-scoped origin projection. Unknown or unverified remote obligations remain live, never reclassified from `reported_status`. Older cores without receipt history retain their existing remote-history limitation.

## Tests

`test/composed.test.mjs` exercises the real session-panel, user-steps, and session-tasks projections together. It covers scheduled relays with blocked/nonblocked remote owners, retired and skipped asks, qualified card opening, and a terminal decision awaiting receipt readback without other open work.

Run with a Desktop dependency installation (React, React DOM, jsdom):

```sh
HERMES_DESKTOP_DIR=/path/to/desktop node --test desktop-plugins/session-panel/test/*.test.mjs
```

Historical incidents require live or preserved renderer state for exact attribution; these regressions establish the visibility failure and its correction, not the cause of every previously red chat.
