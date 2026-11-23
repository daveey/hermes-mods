# Sidebar project membership across gateways and profiles

Patch coverage: **5929**, **5930**, **5931**.

## Behavior

The Desktop project sidebar merges live session rows into a declared project without counting a session twice, even when the project's preview list is limited or its folder path is unknown. Session identity is always owner (connection), profile and durable id, so equal ids or paths on different owners or profiles stay distinct. Complete membership, including sessions outside the preview, survives merging profiles on the backend and gateways in the client. Same-owner group members no longer show machine headings; headings appear only when members span more than one owner.

## How it works

`withGatewayProjectRows` seeds its seen-set with every row already in the node (5929) and, from 5930, with the node's authoritative membership. Folder matching accepts the node path, a repo path or a retained row's folder, so merged nodes with no usable path still absorb live rows; unmatched rows create a new node only if not already claimed elsewhere. From 5931 a node may carry `sessionMembership` entries (`id`, `profile`, optional `connection_id`). The backend `_merge_profile_tree` creates and extends them per profile and unions `sessionIds`; the Electron profile merge and `gateway-project-merge` stamp the owner and carry them through, and claimed-id filtering removes them too. Nodes without `sessionMembership` keep the older rule where bare ids belong to the declared owner and profile. `ProjectGroupRow` shows machine headings only for multi-owner groups.

## Code

- `apps/desktop/src/app/chat/sidebar/projects/gateway-project-rows.ts`: identity keys, membership, folder matching.
- `apps/desktop/src/app/chat/sidebar/projects/presentation-group-row.tsx`: conditional machine headings.
- `apps/desktop/src/app/chat/sidebar/projects/workspace-groups.ts`: `sessionMembership` type.
- `apps/desktop/src/store/gateway-project-merge.ts`: stamping, merging and claim filtering.
- `apps/desktop/electron/profile-session-routing.ts`: profile merge of membership.
- `hermes_cli/web_routers/profiles.py`: `_merge_profile_tree` membership.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live Desktop result is claimed.

- `gateway-project-rows.test.ts`: owner-folder merges without double counting, distinct owners/profiles, retained folders without a path, mixed-owner claims, unhydrated membership, mixed-profile membership after gateway merge.
- `presentation-machine-labels.test.tsx`: no headings for same-owner members in local and remote windows; existing multi-machine labels unchanged.
- `apps/desktop/electron/profile-session-routing.test.ts`: profile tree merge carries membership and ids.
- `tests/hermes_cli/test_merge_profile_tree.py`: membership survives limited previews across profiles.

Untested entry path: a running Desktop window with real gateways; the vitest/node cases build trees by hand.

## Applying

Apply the covered patches only as part of the full ordered core series, following the whole segmented recipe in the [core README Applying section](README.md#applying) (replay bridges and required repair overlays included). The mirror is append-only and these entries depend on earlier ones; do not run a standalone `git am` on them and do not treat them as an independent cherry-pick set. A reimplementation on a newer upstream should keep the invariants above and run the named suites in a disposable source checkout.

## Gotchas

- Identity must include profile; id-only deduplication merges unrelated sessions.
- `sessionIds` is bare, so it is only meaningful alongside a declared owner/profile or `sessionMembership`.
- Older backends omit `sessionMembership`; the client falls back to the legacy rule.
- The Electron, store and Python merges must be changed together.

## Why not a plugin

No: the sidebar tree, gateway merge and profile router are core Desktop and web-router code with no extension point for project membership.
