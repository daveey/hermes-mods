# Node-first Runners: direction B

Approved composition: a native Monitor popover, not a dashboard. Keep the existing bottom-bar trigger, end-aligned upward popover, header, owner scope, refresh, search and filter controls. Replace registration-first inventory with collapsed physical nodes and a separate Unassigned runners disclosure. Expand a node for registrations; select a registration for inline detail. No reserved inspector.

## Layout

Native 12px body and 11px metadata; inherited foreground, ui-text-secondary, ui-bg-elevated, ui-stroke-secondary, ui-accent and chrome-action-hover tokens. Controls retain 4px radii and structural gaps of 8px. Width is min(680px, viewport minus 24px); height is bounded to 70vh, with inventory scrolling. Node identity and OS occupy the leading area; health is at the right. Activity and actual observation age are secondary. Diagnostics follow inventory and start collapsed. Material owner failures remain visible.

Public examples must use fictional identifiers. Original design captures and live fleet evidence are private and are not embedded here. This document is a sanitized implementation specification, not a claim that browser/production acceptance has passed.

## Identity and evidence

- Nodes are keyed by owner gateway/profile plus host.id, never by hostname. Iterate hosts first so empty hosts survive. Match runner.host_id only within the same owner; unmatched runners are not fabricated physical nodes.
- Preserve provider/endpoint/scope/provider-runner identity deduplication, captured action routes, owner epochs, cache ownership and validated job URLs.
- Node age requires actual host provenance, not inventory envelope time or a runner timestamp. Unknown timestamps remain unknown. Collector heartbeat is a separate diagnostic.
- Management reachable does not imply verified node health. A failed probe is not proof of node outage. Provider-offline stays a runner observation.
- Busy/idle/unknown is independent of readiness. Incomplete activity coverage stays unknown, not zero.
- Capacity requires enforced=true and an explicit limit. Unknown active jobs stay unknown. Never subtract registrations to invent free slots.
- Maintenance preserves state, reason and expiry; no implied draining.
- jobs=[] establishes neither idle nor not-configured. Until explicit capability metadata exists, show Job attribution unavailable. Distinguish explicit not-configured, forbidden, unavailable, and configured/busy/unattributed when the data owner supplies that contract.

## Interaction

Expansion starts empty; discovered nodes start collapsed. Polling retains existing expansions/details and prunes removed node identities, not filtered-out ones. Search retains matching parents, includes empty matching hosts, highlights matches and labels filtered counts separately from scope counts. The native palette jumps to nodes/runners and exposes validated settings aliases (configuration/preferences); no second command system.

Arrows traverse visible node and runner buttons. Enter toggles expansion/detail. Right/Left expand/collapse nodes. Slash or typing enters search without losing the first character or resetting the caret. Shift+F10, right-click and overflow retain native context actions. Escape closes menu/help, then detail, query, expanded node and popover, restoring focus. Question mark shows shortcuts.

## Required acceptance gates

1. Two known hosts and unmapped registrations render two collapsed physical nodes plus Unassigned. Empty host remains; no empty inspector.
2. Refresh discovers a collapsed node without closing unrelated detail; stale owner routing never becomes ambient routing.
3. Busy is nonerror. Stale cannot become Ready. Unknown timestamps remain unknown; heartbeat does not freshen row/node age.
4. Failed management is neither runner-offline nor confirmed outage; unbound monitoring is explicit.
5. Deduplicated registration counts match scope/filter cohorts and never imply capacity; unknown activity stays unknown.
6. Empty jobs do not imply idle/configuration. Explicit not-configured, forbidden, unavailable and busy-unattributed have separate tests; job links remain validated.
7. Detail is lazy and owner/identity guarded; failures preserve stale last-known evidence; unsupported repair remains unavailable.
8. Nonowner diagnostics neither dominate inventory nor inflate health counts; actual owner failure remains visible.
9. Real browser keyboard: multi-character query, middle edit, highlight/filter, arrows/Enter, Escape round trip, context-menu Escape, palette jump and scope selection. No dropped input or caret reset.
10. Render at native width, 640px and 390px, in both inherited themes. No horizontal overflow, clipping, hidden focus, detached footer or reserved inspector. Long inventory scrolls without hiding header controls.
11. Existing transport/project/details/keyboard tests pass except intentional collapse expectation changes, with new tests. Prototype/jsdom tests alone do not establish production readiness.

Independent pinned-model review, exact-head CI and actual packaged-UI/live-data keyboard evidence remain release gates. See README for the executable unit-test command.
