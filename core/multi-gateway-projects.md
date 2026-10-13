# Multi-gateway projects (Desktop)

Patches: `0023`, `0024`, `0025`, `0028`, `0029` in `core/patches/`. All touch `apps/desktop` only.

If the connections registry has more than one gateway (for example a laptop plus a `mac-mini`),
the Desktop sidebar already lists sessions from all of them. Upstream, though, the actions on
those rows still go to the **active** gateway. That gateway doesn't own the remote rows, so
archive, move and resume fail with "session not found". Projects also come only from the active
gateway. With these patches the sidebar works across gateways: you see every gateway's projects,
rows are colored by gateway, and each action runs on the gateway that owns the session.

| Patch | Kind | Summary |
|---|---|---|
| 0023 | fix | Archive/unarchive a session on its owning gateway |
| 0024 | feat | Sidebar shows every gateway's projects, colored per gateway, with show/hide toggles |
| 0025 | feat | Move a session into any gateway's project; the project is created on the session's gateway |
| 0028 | fix | Cross-gateway move creates an empty project folder instead of cloning (amends 0025) |
| 0029 | fix | A gateway-qualified `@session` ref is never parsed as a profile name |

Terms:
- **owning gateway**: the registry connection a session row came from (`session.connection_id`).
  A row with no `connection_id` belongs to the registry `primary`, or to `local` if there is none.
- **active gateway**: the connection the window is currently driving.

## Behavior

### 0024: every gateway's projects in one sidebar
- The active gateway's `projects.tree` still paints first and never waits on anything. Each other
  visible gateway's `projects.tree` is fetched in the background. When a fetch lands, the last
  local tree is merged again and repainted.
- Remote fetch cadence, per connection:
  - at most one fetch per **30 s**, because `sessions.changed` fires on every turn;
  - after a failure, a **120 s** backoff, during which that gateway adds nothing;
  - a **20 s** timeout on each request.
- Merging (`mergeGatewayProjectTrees`):
  - The base (active) tree keeps its ids, order and structure.
  - Home matches Home. Any other project matches on its case-folded label, so a repo checked out
    on two machines shows up as **one lane**.
  - In a merged lane, session counts, tokens and cost are summed. Preview rows are deduped and
    sorted newest first. The `sessionIds` sets are unioned.
  - A project that only a remote has gets its **own lane** with the id `gw:<connectionId>:<id>`.
    Its repo and group ids are namespaced the same way. It has `path: null` and `isAuto: true`,
    because a remote folder can't be opened, themed, or used as a cwd from this machine.
  - Archived remote projects are skipped.
  - Every remote row is stamped with its `connection_id`, so clicking it resumes on the right
    gateway.
  - Remote session ids are added to the scoped (live) set.
- Every session row gets a 3 px stripe on its left edge in its gateway's color
  (`data-slot="session-gateway-stripe"`). Stripes only appear when the registry has more than one
  connection.
- Default colors: `kind: 'local'` gets no color. Each remote takes the next slot of an 8-color
  palette (violet, amber, teal, rose, sky, lime, orange, fuchsia) in registry order. Colors are
  therefore the same on every launch, and adding a gateway doesn't change existing ones.
- **Filter menu → Gateways** (shown only with ≥2 connections) has a checkbox per gateway for
  show/hide, plus a per-gateway color submenu that includes **No color**.
- Both settings are saved in localStorage:
  - `hermes.desktop.sidebar.gateways.v1.hidden`: string[] of hidden connection ids;
  - `hermes.desktop.sidebar.gateways.v1.colors`: id → palette key or `none`.
- Hiding a gateway only changes what you see. The gateway stays connected. Its rows disappear
  from every list and lane, and its tree is no longer fetched. Showing it again repaints from
  cache right away, then fetches.

### 0025 + 0028: move a session into any gateway's project
Upstream, `moveSessionToProject` called `session.workspace.move` on the active gateway, so moving
a `mac-mini` session from the laptop failed with "session not found".

1. **Pick the gateway** (`moveTargetGateway`). Find the row in `$sessions` or the merged tree and
   take its `connection_id`; an explicit `connectionId` argument overrides it.
   - If that is the active gateway, nothing changes: the move uses `gatewayRequest`.
   - Otherwise the move goes through `requestGatewayForAgent` with a 60 s timeout. The profile is,
     in order: the caller's argument, then the row's `profile`, then the connection's
     `remoteProfile`, then `default`.
2. **Find the destination folder on that gateway** (`ensureProjectRootOn`):
   - Fast path: a local session moving to a local project uses that project's root cwd. This is
     the upstream behavior.
   - Otherwise call `projects.list` there and reuse a non-archived project whose `name` or `slug`
     matches the lane label (case-insensitive, the same key the merge uses).
   - If none matches, create the project there:
     - take the source project's root, from the local tree or the remote tree cache;
     - map it to a home-relative path: `/Users/x/…` or `/home/x/…` → `~/…`, `/root/…` → `~/…`,
       any other path unchanged;
     - run `mkdir -p` on it through `shell.exec`;
     - call `projects.create { name, primary_path, profile }`.
   - If `mkdir` fails, the move stops with `Could not create <path> on <connectionId>: <stderr>`.
3. Run `session.workspace.move` on the owning gateway with that cwd. Then mark that gateway's
   cached tree stale, so the next refresh shows the move.

`moveToProjectTargets` also offers remote-only (`gw:`) lanes as targets, even though they have no
local cwd. Both the context menu and the ⌘⇧P palette from 0018 use it.

**0028 changes 0025.** As first written, 0025 checked whether the folder existed. If it was
missing, 0025 ran `git remote get-url origin` on the source gateway and `git clone`d that remote
onto the target. 0028 drops both the check and the clone and always runs a plain `mkdir -p`.
Projects here only organize the sidebar, so the folder just has to exist. Cloning a repo onto
another machine as a side effect of a sidebar move was surprising, and it could fail or be slow.

### 0023: archive/unarchive on the owning gateway
Before this fix, archiving a row from another gateway in the All-Gateways sidebar sent the PATCH
to the active backend. The backend returned 404 "Session not found", the optimistic hide was
rolled back, and the row reappeared.
- `setSessionArchived(id, archived, profile)` now takes a `ProfileScope`. An object owner
  `{ connectionId, profile }` sends `PATCH /api/sessions/<id>` to **that** connection, with the
  profile in both the request scope and the body.
- `use-session-actions` builds that owner from the row's `connection_id` and `profile` (default
  `default`) for both archive and restore. This mirrors how `deleteSession` routes through
  `removedOwner`. Rows without a `connection_id` behave as before.

### 0029: a `profile@gateway` session ref is never a profile
The session-mentions plugin inserts cross-gateway refs of the form
`@session:<profile>@<gateway>/<id>`. Desktop split refs at the `/` and read `default@mac-mini` as
the profile. That opened a phantom pool backend `conn:mac-mini::default@mac-mini`, and that
backend stamped the bogus profile onto every `mac-mini` sidebar row. Resume, move and archive on
those rows then failed with `Profile 'default@mac-mini' does not exist.`

Profile names can't contain `@` (backend rule `[a-z0-9][a-z0-9_-]*`). The patch fixes the bug in
four places:
- **Ref parsing.** `parseSessionRefValue` returns `{ gateway?, profile?, sessionId }`.
  Everything from the first `@` is routing, not profile. A stacked qualifier
  (`default@mac-mini@mac-mini`) uses the last segment as the gateway. The cache key writes the
  qualifier back out (`profile@gateway/id`).
- **Backend pool.** `ensureRegistryBackend` throws for any profile key that fails
  `PROFILE_NAME_RE`. The sidebar aggregator skips existing pool keys whose profile part is
  invalid.
- **Owner hints.** A session owner hint is not stored if its `profile` or `targetProfile`
  contains `@`.
- **Tile routes.** Persisted tile owner routes that contain `@` are dropped on hydrate and
  resolved again. Valid routes are kept.

## How it works

No RPC contract or Python changes. Everything is renderer state plus one Electron-main guard. The
backend methods used all exist upstream in `tui_gateway/`: `projects.tree`, `projects.list`,
`projects.create`, `shell.exec` and `session.workspace.move`.

- **Routing primitive.** `requestGatewayForAgent(connectionId, profile, method, params, timeoutMs)`
  in `apps/desktop/src/store/gateway.ts` (upstream) sends a JSON-RPC call to any registry
  connection. `gatewayRequest` only reaches the active one. HTTP calls (`hermesApi` in
  `src/api/*.ts`) route through `sessionScoped(scope)`, which accepts a `ProfileScope`: a string
  or `{ connectionId, profile }`.
- **Owner stamping.** `stampRowsWithOwningConnection` (`src/lib/session-owner-stamp.ts`,
  upstream) adds a `connection_id` to untagged rows. It never stamps `local`.
- **0024**
  - `src/store/gateway-visibility.ts` (new): persistent atoms, palette, computed
    `$gatewayColors` / `$gatewayColorsActive`, and `sessionGatewayId` / `sessionOnHiddenGateway`.
  - `src/store/gateway-project-merge.ts` (new): pure `mergeGatewayProjectTrees` plus the `gw:` id
    helpers.
  - `src/store/projects.ts`: the `remoteTrees` cache, `refreshRemoteProjectTrees`, and
    `lastLocalTree` (so a late remote answer can repaint). `applyProjectTreePayload` now merges.
  - UI: `src/app/chat/sidebar/filter-menu.tsx` (`GatewaysSubmenu`), `sidebar/index.tsx` (drops
    rows on hidden gateways), `sidebar/session-row.tsx` (the stripe).
- **0025/0028** (`src/store/projects.ts`): `moveTargetGateway` → `ensureProjectRootOn` →
  `moveTargetRequest('session.workspace.move')` → `expireRemoteProjectTree`.
  `moveToProjectTargets` accepts `gw:` lanes.
- **0023**: `setSessionArchived` in `src/api/sessions.ts` accepts an object owner. The callers in
  `src/app/session/hooks/use-session-actions/index.ts` build that owner from the row.
- **0029**: `src/lib/session-refs.ts` (parser), `electron/main.ts` (`ensureRegistryBackend`
  guard and pool-key skip), `src/store/session.ts` (owner hints), `src/store/session-states.ts`
  (persisted tile owner routes).

## Code

Excerpts below are condensed (types and comments trimmed). The patches are authoritative.

### `apps/desktop/src/store/gateway-project-merge.ts` (0024, new)
```ts
const REMOTE_ID_PREFIX = 'gw:'
export const remoteProjectId = (connectionId: string, id: string) => `${REMOTE_ID_PREFIX}${connectionId}:${id}`
export const isRemoteProjectId = (id: null | string | undefined) => Boolean(id && id.startsWith(REMOTE_ID_PREFIX))

const projectKey = (p: SidebarProjectTree) =>
  p.isNoProject ? '\u0000home' : `label:${p.label.trim().toLowerCase()}`

export function mergeGatewayProjectTrees(base, remotes: GatewayProjectTree[], previewLimit: number) {
  if (!remotes.length) return base
  const out = [...base]
  const indexByKey = new Map<string, number>()
  out.forEach((p, i) => { if (!p.archived && !indexByKey.has(projectKey(p))) indexByKey.set(projectKey(p), i) })

  for (const { connectionId, projects } of remotes) {
    for (const raw of projects) {
      if (raw.archived) continue
      const remote = stampTree(raw, connectionId)      // stampRowsWithOwningConnection on previews + repo groups
      const at = indexByKey.get(projectKey(remote))
      if (at !== undefined) { out[at] = mergeInto(out[at], remote, previewLimit); continue }
      indexByKey.set(projectKey(remote), out.length)
      out.push({ ...remote, id: remoteProjectId(connectionId, remote.id), path: null, isAuto: true,
                 repos: namespaceRepos(remote, connectionId) })   // repo.id and group.id also get gw: prefix
    }
  }
  return out
}
// mergeInto: sum sessionCount/totalTokens/totalCostUsd, max lastActive, dedupe previews by id,
// sort by last_active ?? started_at desc, slice(previewLimit), union sessionIds.
export const remoteScopedSessionIds = (remotes) => remotes.flatMap(r => r.projects.flatMap(p => p.sessionIds ?? []))
```

### `apps/desktop/src/store/gateway-visibility.ts` (0024, new)
```ts
const PREFIX = 'hermes.desktop.sidebar.gateways.v1'
export const $hiddenGatewayIds = persistentAtom<string[]>(`${PREFIX}.hidden`, [], Codecs.stringArray)
export const $gatewayColorOverrides = persistentAtom<Record<string, string>>(`${PREFIX}.colors`, {}, Codecs.stringRecord)
export const GATEWAY_PALETTE = [
  { id: 'violet', color: '#a78bfa' }, { id: 'amber', color: '#f59e0b' }, { id: 'teal', color: '#2dd4bf' },
  { id: 'rose', color: '#fb7185' }, { id: 'sky', color: '#38bdf8' }, { id: 'lime', color: '#a3e635' },
  { id: 'orange', color: '#fb923c' }, { id: 'fuchsia', color: '#e879f9' }]
export const GATEWAY_COLOR_NONE = 'none'

export const sessionGatewayId = (session, registry) =>
  session.connection_id?.trim() || registry?.primary || 'local'

export function defaultGatewayColors(connections) {   // local → null, remotes → palette slot in order
  const out = {}; let slot = 0
  for (const c of connections) out[c.id] = c.kind === 'local' ? null : GATEWAY_PALETTE[slot++ % GATEWAY_PALETTE.length].id
  return out
}
// resolveGatewayColorIds(registry, overrides): defaults, then 'none' → null, valid palette key → key.
export const $gatewayColors = computed([$connectionsRegistry, $gatewayColorOverrides], /* id → CSS color | null */)
export const $gatewayColorsActive = computed($connectionsRegistry, r => (r?.connections.length ?? 0) > 1)
export const sessionOnHiddenGateway = (session, hidden, registry) =>
  hidden.length > 0 && hidden.includes(sessionGatewayId(session, registry))
```

### `apps/desktop/src/store/projects.ts`: remote tree cache (0024)
```ts
const REMOTE_TREE_FRESH_MS = 30_000, REMOTE_TREE_FAILURE_BACKOFF_MS = 120_000, REMOTE_TREE_TIMEOUT_MS = 20_000
const remoteTrees = new Map<string, { projects; fetchedAt; failedAt; inFlight }>()
let lastLocalTree: { res: ProjectTreePayload; owner: ProjectRowOwner } | null = null
const ownerGatewayId = (owner) => owner.connectionId || $connectionsRegistry.get()?.primary || 'local'

async function refreshRemoteProjectTree(connectionId: string, profile: string) {
  const now = Date.now(); const e = remoteTrees.get(connectionId) ?? { projects: [], fetchedAt: 0, failedAt: 0, inFlight: false }
  if (e.inFlight || now - e.fetchedAt < REMOTE_TREE_FRESH_MS || now - e.failedAt < REMOTE_TREE_FAILURE_BACKOFF_MS) return
  e.inFlight = true; remoteTrees.set(connectionId, e)
  try {
    const res = await requestGatewayForAgent<ProjectTreePayload>(connectionId, profile, 'projects.tree',
      { preview_limit: projectTreePreviewLimit() }, REMOTE_TREE_TIMEOUT_MS)
    e.projects = res.projects ?? []; e.fetchedAt = Date.now(); reapplyLastProjectTree()
  } catch { e.failedAt = Date.now() } finally { e.inFlight = false }
}
// refreshRemoteProjectTrees(owner): for each registry connection except self and hidden,
//   refreshRemoteProjectTree(id, normalizeProfileKey(connection.remoteProfile || 'default')).
//   Called after applyProjectTreePayload in refreshProjectTreeOn and refreshProjectTreeAcrossProfiles.
$hiddenGatewayIds.listen(() => { reapplyLastProjectTree(); if (lastLocalTree) refreshRemoteProjectTrees(lastLocalTree.owner) })

function applyProjectTreePayload(res, owner) {
  lastLocalTree = { res, owner }
  const remotes = visibleRemoteTrees(owner)            // cached, non-self, non-hidden, non-empty
  const scoped = new Set([...(res.scoped_session_ids ?? []), ...remoteScopedSessionIds(remotes)])
  $projectTree.set(replaceEqualDeep($projectTree.get(), mergeGatewayProjectTrees(
    (res.projects ?? []).map(p => tagProjectSessionConnection(p, owner)), remotes, projectTreePreviewLimit())))
  // ...unchanged
}
// fetchProjectSessions(projectId): if isRemoteProjectId(projectId) return the tree node as is.
```
Session row stripe (`sidebar/session-row.tsx`):
```tsx
const gatewayId = sessionGatewayId(session, gatewayRegistry)
const gatewayColor = gatewayColorsActive ? (gatewayColors[gatewayId] ?? null) : null
{gatewayColor && <span aria-hidden="true" data-slot="session-gateway-stripe" data-gateway-id={gatewayId}
  className="pointer-events-none absolute top-1 bottom-1 left-0 w-[3px] rounded-full"
  style={{ backgroundColor: gatewayColor }} />}
```
In `filter-menu.tsx`, `GatewaysSubmenu` returns `null` below 2 connections. Each gateway gets a
`DropdownMenuCheckboxItem` that calls `toggleGatewayHidden`, and a `DropdownMenuRadioGroup` that
calls `setGatewayColor`. `sidebar/index.tsx` drops rows where `sessionOnHiddenGateway(...)` is true
and counts `hiddenGatewayIds.length > 0` as an active filter.

### `apps/desktop/src/store/projects.ts`: cross-gateway move (0025 as amended by 0028)
```ts
function moveTargetGateway(sessionId, profile?, connectionId?): MoveTarget {
  const row = $sessions.get().find(s => sessionMatchesStoredId(s, sessionId))
           ?? treeSessionRows().find(s => sessionMatchesStoredId(s, sessionId))
  const conn = (connectionId ?? row?.connection_id ?? '').trim()
  const self = ownerGatewayId(projectRowOwner())
  if (!conn || conn === self) return { connectionId: self, profile: profile ?? null, remote: false }
  const remoteProfile = $connectionsRegistry.get()?.connections.find(c => c.id === conn)?.remoteProfile
  return { connectionId: conn, profile: normalizeProfileKey(profile || row?.profile || remoteProfile || 'default'), remote: true }
}
const moveTargetRequest = (t, method, params) => t.remote
  ? requestGatewayForAgent(t.connectionId, t.profile || 'default', method, params, 60_000)
  : gatewayRequest(method, params)

export function homeRelativePath(path: string): string {
  const m = path.match(/^(?:\/Users|\/home)\/[^/]+(\/.*)?$/) ?? path.match(/^\/root(\/.*)?$/)
  return m ? `~${m[1] ?? ''}` : path
}
function shellPath(path) {           // keep ~/ expandable, single-quote the rest
  const q = s => `'${s.replace(/'/g, `'\\''`)}'`
  return path.startsWith('~/') ? `"$HOME"/${q(path.slice(2))}` : q(path)
}

async function ensureProjectRootOn(target, node) {
  if (!node || node.isNoProject) return ''
  const profile = target.profile || (target.remote ? 'default' : writableProjectProfile())
  const listed = await moveTargetRequest(target, 'projects.list', { profile })
  const existing = (listed.projects ?? []).find(p => !p.archived &&
    (labelKey(p.name) === labelKey(node.label) || labelKey(p.slug) === labelKey(node.label)))
  const existingPath = existing?.primary_path || existing?.folders?.[0]?.path
  if (existingPath) return existingPath
  const source = projectSourceFor(node)   // local root cwd, or a same-label project in remoteTrees (gw:<conn>: narrows it)
  if (!source) return ''
  const path = homeRelativePath(source.path)
  const make = await moveTargetRequest(target, 'shell.exec', { command: `mkdir -p ${shellPath(path)}` })
  if (make.code !== 0) throw new Error(`Could not create ${path} on ${target.connectionId}: ${(make.stderr || make.stdout).trim()}`)
  const created = await moveTargetRequest(target, 'projects.create', { name: node.label, primary_path: path, profile })
  target.remote ? expireRemoteProjectTree(target.connectionId) : void refreshProjects()
  return created.project?.primary_path || path
}

export async function moveSessionToProject(sessionId, projectId, profile?, connectionId?) {
  const node = $projectTree.get().find(n => n.id === projectId)
  const target = moveTargetGateway(sessionId, profile, connectionId)
  const cwd = !target.remote && node && !isRemoteProjectId(node.id) && projectRootCwd(node)
    ? projectRootCwd(node)                       // upstream fast path
    : await ensureProjectRootOn(target, node)
  if (!cwd) throw new Error(translateNow('sidebar.projects.moveNoFolder'))
  suppressMoveFollow(cwd)                         // from 0004
  const res = await moveTargetRequest(target, 'session.workspace.move',
    { cwd, session_key: sessionId, ...(target.profile ? { profile: target.profile } : {}) })
  if (target.remote) expireRemoteProjectTree(target.connectionId)   // sets fetchedAt = 0
  // ...unchanged
}
// moveToProjectTargets: node.id !== current && !node.isNoProject && (projectRootCwd(node) || isRemoteProjectId(node.id))
```

### `apps/desktop/src/api/sessions.ts` + `use-session-actions/index.ts` (0023)
```ts
export function setSessionArchived(id: string, archived: boolean, profile?: ProfileScope) {
  if (profile && typeof profile === 'object') {
    const owner = sessionWriteProfile(profile.profile)
    return hermesApi<{ ok: boolean }>({ ...sessionScoped(profile), ...(owner ? { profile: owner } : {}),
      path: `/api/sessions/${encodeURIComponent(id)}`, method: 'PATCH',
      body: { archived, ...(owner ? { profile: owner } : {}) } })
  }
  // ...string path unchanged
}
// archiveSession / unarchiveSession:
const archiveOwner: SessionOwnerScope = archived?.connection_id
  ? { connectionId: archived.connection_id, profile: archived.profile || 'default' }
  : profile
await setSessionArchived(storedSessionId, true /* false for restore */, archiveOwner)
```

### Session-ref parsing and guards (0029)
```ts
// src/lib/session-refs.ts
export function parseSessionRefValue(value: string): { gateway?: string; profile?: string; sessionId: string } {
  const trimmed = value.trim(); const slash = trimmed.indexOf('/')
  if (slash === -1) return { sessionId: trimmed }
  const head = trimmed.slice(0, slash).trim(); const sessionId = trimmed.slice(slash + 1).trim()
  if (!sessionId) return { sessionId: trimmed }
  const at = head.indexOf('@')
  const profile = (at === -1 ? head : head.slice(0, at)).trim()
  const gateway = at === -1 ? '' : head.slice(head.lastIndexOf('@') + 1).trim()   // stacked → last segment
  return { ...(gateway ? { gateway } : {}), profile: profile || undefined, sessionId }
}
// sessionRefCacheKey → `${profile ?? ''}${gateway ? `@${gateway}` : ''}/${sessionId}`
```
```ts
// electron/main.ts, ensureRegistryBackend()
if (!PROFILE_NAME_RE.test(profileKey)) throw new Error(`Profile '${profileKey}' does not exist.`)
// electron/main.ts, pooledRegistrySessionSources(): skip pool keys with an invalid profile part
if (perProfile && !PROFILE_NAME_RE.test(key.slice(prefix.length) || 'default')) continue
```
```ts
// src/store/session.ts, rememberSessionOwnerHint()
if (!id || !normalized.connectionId || normalized.profile.includes('@') || normalized.targetProfile?.includes('@')) return false
// src/store/session-states.ts, parseTileList(): keep ownerRoute only if neither profile nor targetProfile contains '@'
```

### i18n (0024)
`src/i18n/en.ts` and `src/i18n/types.ts` gain three keys:
- `sidebar.filter.gateways`: `'Gateways'`
- `sidebar.filter.gatewayColor`: `` (name) => `${name} color` ``
- `sidebar.filter.noColor`: `'No color'`

No other locale file changes, and `overlay-gaps.json` isn't updated (see Gotchas).

## Tests

New or changed vitest files, all under `apps/desktop/src/`:

| Patch | File |
|---|---|
| 0023 | `api/sessions.test.ts`, `app/session/hooks/use-session-actions/unarchive-session.test.tsx` |
| 0024 | `store/gateway-project-merge.test.ts`, `store/gateway-visibility.test.ts` (new) |
| 0025/0028 | `store/projects-cross-gateway-move.test.ts` (see below) |
| 0029 | `lib/session-refs.test.ts`, `store/session-owner-hint-qualified.test.ts` (new) |

`projects-cross-gateway-move.test.ts` is new in 0025. 0028 replaces its clone cases. It covers:
- a local control case;
- a remote session creating the project on its gateway;
- reusing an existing same-named remote project;
- home-path mapping;
- remote-only lanes offered as targets;
- an empty folder created with no clone;
- a failed `mkdir` stopping the move.

Run from `apps/desktop` (its `test` script is `vitest run`):
```sh
cd apps/desktop
npx vitest run src/store/gateway-project-merge.test.ts src/store/gateway-visibility.test.ts \
  src/store/projects-cross-gateway-move.test.ts src/api/sessions.test.ts \
  src/app/session/hooks/use-session-actions/unarchive-session.test.tsx \
  src/lib/session-refs.test.ts src/store/session-owner-hint-qualified.test.ts \
  src/i18n/overlay-completeness.test.ts
npm run typecheck
```
Verified on `upstream/main` with `0004 0018 0023 0024 0025 0028 0029` applied: all 7 feature test
files pass (63 tests). `overlay-completeness.test.ts` fails, as described under Gotchas.

## Applying

Order: `0023 → 0024 → 0025 → 0028 → 0029`.
- 0028 rewrites code that 0025 added.
- 0025 needs 0024 (`remoteTrees`, `ownerGatewayId`, `isRemoteProjectId`).
- 0023 and 0029 can each be applied on their own.

Textual dependencies on earlier patches (checked against `upstream/main`):
- **0004** (moving a session into a project doesn't switch to it). Without it, 0024 conflicts in
  `store/projects.ts`. 0025 also relies on the `suppressMoveFollow` that 0004 adds.
- **0018** (⌘⇧P palette move). Without it, 0025 conflicts in `store/projects.ts`.

The minimal set that applies cleanly:
```sh
cd ~/.hermes/hermes-agent        # your checkout
for n in 0004 0018 0023 0024 0025 0028 0029; do
  git am -3 ~/code/hermes-mods/core/patches/$n-*.patch || break
done
```
Applying the whole series in order also works, except 0013, which conflicts in
`tui_gateway/server.py` and is unrelated to this feature.

To re-implement on a newer upstream instead, work from the Code section. It relies only on these
upstream pieces:
- `requestGatewayForAgent`
- `stampRowsWithOwningConnection`
- `ProfileScope` / `sessionScoped`
- `persistentAtom` / `Codecs`
- the five RPC methods named in How it works

## Gotchas

- **`overlay-completeness.test.ts` fails after 0024.** The three new `sidebar.filter.*` keys exist
  only in `en.ts`, and `src/i18n/overlay-gaps.json` doesn't list them. Either add translations to
  each locale overlay (`ar de es fr ja ru zh zh-hant`), or regenerate the gap list from
  `apps/desktop` with `OVERLAY_GAPS_UPDATE=1 npx vitest run src/i18n/overlay-completeness.test.ts`.
  The UI isn't affected, because missing keys fall back to English through `defineLocale`.
- **No generated contract to rebuild.** No RPC method or schema changes, so
  `apps/shared/src/gateway-contract.generated.ts` and `openrpc.json` stay as they are.
- **The remote gateway must allow `shell.exec`.** Creating a missing project on the session's
  gateway runs `mkdir -p` through `shell.exec` (`tui_gateway/methods_tools.py`). That handler runs
  a `shell=True` subprocess with a 30 s timeout and a dangerous-command filter. If the call is
  blocked or unavailable, the move fails with the `Could not create …` error. Reusing an existing
  same-named project never calls it.
- **Label is the only identity.** Two unrelated projects with the same name on different
  gateways merge into one lane, and a move reuses the same-named project on the destination.
- **No code is copied.** A cross-gateway move creates the folder empty; it never clones or syncs.
  If you want the code on the other machine, clone it there yourself.
- **Remote trees are snapshots** up to 30 s old. A move expires the target gateway's cache right
  away. Other changes show up on the next throttled fetch.
- **Remote-only lanes have no local path.** Open-folder, theming and cwd actions don't apply to
  them, and `fetchProjectSessions` returns the cached node instead of hydrating it.
- **Colors key on registry order and ids.** Reordering the registry changes the default palette
  slots. Renaming a connection id loses its color override and hidden state.
- **Rebuild the app.** These are renderer and Electron-main changes, so run
  `hermes desktop --build-only --force-build`. On macOS, set `APPLE_SIGNING_IDENTITY`, or the app
  is ad-hoc signed.
- **`hermes update` with local commits.** A fork carrying these commits can't fast-forward on
  update. It needs a merge flow (see patch 0007, `updates.fork_merge_upstream`).

## Could this be a plugin instead?

Mostly not. The desktop plugin SDK (`@hermes/plugin-sdk`: `KEYBINDS_AREA`, `PALETTE_AREA`,
composer areas; see `desktop-plugins/spin-out/plugin.js`) can add UI and actions. It can't:
- replace `$projectTree` or `applyProjectTreePayload`;
- restyle or filter the built-in sidebar rows (the stripe, the hidden-gateway filter);
- reroute the built-in archive and move handlers;
- touch Electron-main's backend pool.

0023 and 0029 are core routing and parsing fixes that a plugin has no hook into. A plugin could
offer a separate "move to project on owning gateway" palette action, but only if the SDK exposes a
per-connection RPC like `requestGatewayForAgent`. Even then, the built-in sidebar would still
show only the active gateway's projects, and the built-in actions would still go to the wrong
gateway. The clean path is to upstream all five patches.
