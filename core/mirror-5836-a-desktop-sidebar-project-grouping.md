# Desktop sidebar: project presentation grouped across gateways (mirror 5523–5525)

## Behavior
- Projects sharing a trimmed, case-folded name share one presentation header, filter/dismissal identity and entered view, while each original backend node keeps its own placement and expansion pipeline. Primary metadata and new-chat routing prefer the window gateway; each entered member keeps its own git and worktree route; group reads do not supersede each other.
- Owner maps, hidden counts, preview membership and Home are not re-derived; store-merged row multiplicities and foreign hydration limits are unchanged.
- Member-specific machine labels, base project menus/context menus and path-less worktree guards are preserved. Members are dismissed individually; group hide is limited to auto-discovered members. Singleton groups render identically to ungrouped projects.

## How it works
Presentation-only seam in `entered-project-members.tsx`, `projects/presentation-group-*.tsx`, `presentation-navigation.ts`, `overview-row.tsx`, `filter-menu.tsx` and `sidebar/index.tsx`.

## Code
`apps/desktop/src/app/chat/sidebar/` as listed.

## Tests
By test names/fixtures: `entered-project-members.test.tsx` (entering either member renders independent same-path base pipelines once; an entered empty primary keeps the foreign member body; a foreign-only name enters both raw pipelines), `gateway-project-member-blocks.test.tsx` (every member equals its unfolded base block, parametrized), oracle suites with JSON fixtures (`gateway-project-{member,nested,overview,placement}-oracle`; ownership, unfolded overview and placement-before-folding), `presentation-entered-git.test.tsx` (member sections, per-owner worktree lanes, member load failures isolated), `presentation-navigation-integration.test.tsx` (group dismissal and undo, navigation cell inventory, stale member scope exit, recents virtualization), `presentation-singleton-parity.test.tsx` (a hash fixture with 24 seeded overview cells), `presentation-entered-singleton-parity.test.tsx` (5524; a hash fixture with only 4 entries: cells 0, 1, 3, 26), `presentation-member-actions.test.tsx`, `presentation-member-worktree.test.tsx`, `projects/presentation-machine-labels.test.tsx`, plus `presentation-groups*.test.ts(x)`, `presentation-navigation.test.ts`, `use-entered-project-sessions.test.tsx` (members hydrate independently without supersession; singleton fetch arguments unchanged) and `sessions-section-entered-groups.test.tsx`. Not executed in this mirror pass.

## Applying
Apply in order; 5525 fixes regressions found in review of 5523.

## Gotchas
Large JSON oracle fixtures (several hundred to a few thousand lines each) ship with the patch; the singleton-parity fixtures are hashes of rendered DOM, so they cannot be read as behaviour descriptions.

## plugin?
No (Desktop app code).
