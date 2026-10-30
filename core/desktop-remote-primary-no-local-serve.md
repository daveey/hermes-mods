# Desktop: a remote-primary window never cold-starts a local serve

Patches: 1208 1209 1210

## Behavior

A desktop window whose primary gateway is a remote (env override or legacy global remote, so no registry-backed
primary connection) used to start a second, local `hermes serve` that nobody asked for. A core E2E remote-topology
check failed with "the client spawned no backend of its own". The retained-Kanban refresh in the session list now
sends no connection pin for rows that lack a `connection_id` when the primary has no real registry connection id.
Rows that do carry a `connection_id`, and windows with a registry-backed primary, behave as before.

## How it works

`primaryGatewayConnectionId()` returns `g.primaryConnectionId ?? 'local'`. That `'local'` fallback names the
"This device" registry entry, and Electron force-spawns a local backend for that entry when the window's primary is
a remote. Using the fallback as a request pin therefore triggers the spawn.

- 1208 adds a null-returning getter and uses it for the pin: `connectionId: row.connection_id ?? primaryPin`.
  Untagged rows are still matched against the fallback id (`primaryConnection`), but the request pin is `null`
  when there is no real primary id.
- 1209 reworks this to stay within the file's line cap: it folds the getter into
  `primaryGatewayConnectionId('pin')` and deletes the separate getter and its long comment.
- 1210 reverts that widening. Another caller on main passes `primaryGatewayConnectionId()` where a `string` is
  required, so the `null | string` return broke the typecheck on merge. The final state has two one-line arrow
  exports in `store/gateway.ts`: `primaryGatewayConnectionId = (): string => g.primaryConnectionId ?? 'local'` and
  `pinnedPrimaryGatewayConnectionId = (): null | string => g.primaryConnectionId`.

Re-implementation rule: any renderer request pin derived from the primary must use the null-returning getter. The
`'local'` fallback is only for labelling or matching untagged rows.

## Code

- `apps/desktop/src/store/gateway.ts`: `primaryGatewayConnectionId`, `pinnedPrimaryGatewayConnectionId`.
- `apps/desktop/src/app/session/hooks/use-session-list-actions.ts`: `refreshSessions` retained-Kanban loop
  (`refreshRetainedKanban(retained, row => getSession(...))`). It reads the pin inside the activation-epoch
  `do { } while` loop, so the pin is re-read on every retry.

## Tests

`apps/desktop/src/app/session/hooks/use-session-list-actions.test.tsx`, added by 1208: "reads a legacy primary's
retained row unpinned, never pinned to the "This device" entry". It asserts
`getSession` is called with `{ connectionId: null, profile: 'default' }` when the row has no `connection_id` and no
registry-backed primary. The 1208 message says the test is red on the base and green after the patch. 1209 and 1210
add no tests; they rely on this one and on the typecheck.

Untested: the 1208 message also says `hubGet()` of the hub-replica connection (hub-sync sidebar) was made passive,
but the 1208 diff contains no such change and no test covers it. Check whether your upstream already has that
change. The E2E claim (remote-topology, remote-secondary pass) is from the commit message only. No test in these
patches covers it, and it was not re-run for this mirror.

## Applying

The whole series is applied at the merge-base with `git am -3 --empty=keep`. Order matters: 1209 edits 1208's
getter and call site, and 1210 edits 1209's result. 1208 depends on earlier patches in the series that created
the hub-sync sidebar and the retained-Kanban refresh.

## Gotchas

- 1209 and 1210 are follow-ups to 1208. 1209 keeps `gateway.ts` under its file-size (line cap) gate; 1210 fixes the
  typecheck regression 1209 introduced by widening `primaryGatewayConnectionId()` to `null | string`.
- Do not widen `primaryGatewayConnectionId()`. Other callers (primary-recents) need a `string`.
- When re-implementing against a newer upstream, keep `gateway.ts` at its line cap. Use one-line exports rather than
  long doc comments.
