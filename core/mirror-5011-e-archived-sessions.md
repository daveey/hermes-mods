# Policy-aware archived-session paging

Mirror entries: 4764, 4765, 4766.

## Behavior

Archived sidebar pages can exclude automated sessions before pagination and counting. Settings continues to manage every archived row. Removing a row affects only its connection/profile/session identity.

## How it works

Both session-list routes accept automated=exclude, translating each store policy into matching SQL exclusions and totals. Policy is part of the scan cache key. Desktop sends the option while automated rows are hidden and restarts its cursor when the preference changes. A stranded old-scope read cannot keep the new scope load-more control disabled. Settings rereads pages when removal races an in-flight page and uses full row identity for removal sets, optimistic filtering, busy flags and React keys.

## Code

- `apps/desktop/src/api/sessions.ts`
- `apps/desktop/src/app/chat/sidebar/archived-view.ts`
- `apps/desktop/src/app/settings/sessions-settings.tsx`
- `apps/desktop/src/store/sidebar-archive.ts`
- `hermes_cli/session_listing.py`
- `hermes_cli/web_routers/profiles.py`
- `hermes_cli/web_routers/sessions.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Backend exclusion tests cover both routes, wildcard policies, source spelling, interactive exceptions, paging totals, policy changes and invalid values. Desktop API, routing, sidebar cursor, integration and settings tests cover query propagation, preference changes, removal races and equal IDs across profiles.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `apps/desktop/electron/profile-session-routing.test.ts`
- `apps/desktop/src/api/sessions.test.ts`
- `apps/desktop/src/app/chat/sidebar/chat-sidebar.integration.test.tsx`
- `apps/desktop/src/app/settings/sessions-settings.test.tsx`
- `apps/desktop/src/store/sidebar-archive.test.ts`
- `tests/hermes_cli/test_sessions_automated_exclude.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The exclusion also removes pinned or attention-marked automated rows from this archived view. Older servers may ignore the option, leaving client filtering as compatibility behavior. Deep pages still reread their prefix; no end-to-end latency improvement is asserted.
