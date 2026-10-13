# Project palette: ⌘⇧P moves a session to a project

Press **⌘⇧P** (Ctrl+Shift+P off macOS) with a session selected and the command palette opens as a
typeahead over your projects. Pick one and the session is re-homed into that project's folder. The
sidebar stays where it was; it does not jump into the destination project. If the typed name matches no
project, the palette offers **Create project "…"**. That creates an empty `~/code/<slug>` folder and
project on the session's own gateway, then moves the session there. All changes are in the desktop app
(`apps/desktop/src/`). No backend or RPC-contract changes.

| Patch | What it does |
|---|---|
| [`0004`](patches/0004-fix-desktop-moving-a-session-into-a-project-doesn-t-.patch) | Moving a session into a project no longer switches the sidebar to that project. |
| [`0018`](patches/0018-feat-desktop-P-moves-the-selected-session-to-a-proje.patch) | ⌘⇧P opens a command-palette page that moves the selected session to a project. |
| [`0030`](patches/0030-feat-desktop-P-offers-Create-project-for-a-typed-nam.patch) | When the typed name matches no project, the page offers **Create project "…"**. |

## Behavior

- **Hotkey:** keybind action `session.moveToProject`, default `mod+shift+p`. You can rebind it in the
  shortcuts panel like any other action.
- **Target session:** the selected stored session. If none is selected, the focused one. If there is
  neither, the handler returns `false` (unhandled), so the key passes through. The session's `profile`
  is captured from `$sessions` with the id.
- **Page:** the palette opens directly on a page named `move-to-project`.
  - Title: `sidebar.projects.moveToProject`.
  - Placeholder: `Move "<session title>" to…`.
  - Opening the page calls `refreshProjectTree()`, because the tree only warms on a timer in the flat
    sidebar view.
- **Candidates:** `moveToProjectTargets(tree, sessions, sessionId)` returns every project with a root
  folder, except the project that already owns the session's cwd and the folderless Home bucket
  (`isNoProject`). The sidebar row menu's "Move to project" submenu now uses the same function.
- **Picking a project:** calls `moveSessionToProject(sessionId, projectId, profile)`.
  - On success: a 2 s success toast `movedTo(label)`.
  - On error: `notifyError(err, moveFailed)`.
- **Create row (0030):** appears as a second, headingless group when the trimmed search text is
  non-empty and matches no `$projectTree` label (case-insensitive, trimmed). Running it calls
  `createProjectAndMoveSession(sessionId, typed, profile)`:
  1. Resolve the session's gateway (local or remote) with `moveTargetGateway`.
  2. Call `projects.list` on that gateway. If a non-archived project's `name` or `slug` matches the typed
     text (case-insensitive), reuse its `primary_path` (or its first folder).
  3. Otherwise run `shell.exec` `mkdir -p "$HOME"/'code/<slug>'`, then `projects.create {name, primary_path:
     '~/code/<slug>', profile}`.
  4. Move the session into that folder (`session.workspace.move`).
- **Esc / Back** on this page closes the palette (`STANDALONE_PAGES`). It does not go back to the root
  command list. Closing the palette clears `$moveToProjectTarget`.
- **Sidebar does not follow (0004):** a move updates the session's cwd. That update normally makes the
  sidebar switch to the destination project. For moves, it is suppressed once per destination cwd, for
  up to 15 s. cwd changes made by the agent still follow as before.

### Slug rule (`newProjectFolder`)

1. Trim and lowercase the name.
2. Replace each run of characters other than Unicode letters/digits, `.`, `_` and `-` with `-`.
3. Strip leading `-`/`.` and trailing `-`.
4. If nothing is left, use `project`.

| Name | Folder |
|---|---|
| `My Cool Project!` | `~/code/my-cool-project` |
| `../x` | `~/code/x` |
| blank | `~/code/project` |

The folder is always under `~/code` on the target gateway's host. No setting changes this.

## How it works

Everything is in the desktop renderer. The backend RPCs already exist upstream:

| RPC | Defined in |
|---|---|
| `session.workspace.move` | `tui_gateway/methods_session.py` |
| `projects.list`, `projects.create` | `tui_gateway/methods_projects.py` |
| `shell.exec` | `tui_gateway/methods_tools.py` |

```
use-keybinds.ts 'session.moveToProject'
  └─ store/command-palette.ts openMoveToProjectPalette({sessionId, profile})
       ├─ $moveToProjectTarget.set(...)
       └─ openCommandPalettePage('move-to-project')
app/command-palette/index.tsx movePage (useMemo on liveSessions, moveTarget, projectTree, search, t)
  ├─ group 1: moveToProjectTargets(...)  → moveSessionToProject(...)
  └─ group 2 (0030): typedIsNew          → createProjectAndMoveSession(...)
store/projects.ts
  moveSessionToProject / createProjectAndMoveSession
    └─ moveSessionToCwd(target, sessionId, cwd)          (0030 factors this out)
         ├─ suppressMoveFollow(cwd)                        (0004)
         ├─ moveTargetRequest(target, 'session.workspace.move', …)
         ├─ suppressMoveFollow(res.cwd) if normalized      (0004)
         └─ setSessions(...) cwd/git fields; refreshProjectTree()
  followActiveSessionCwd(cwd)  ← session.info cwd echo
    └─ return early if consumeMoveFollowSuppression(cwd)  (0004)
```

- **Follow suppression (0004).** `session.workspace.move` makes the backend emit a `session.info` cwd
  change for the session. `followActiveSessionCwd` would otherwise switch `$projectScope` into the
  destination project.
  - Before the RPC, the destination is stored in a module-level `Map<cwd, expiry>`, with expiry 15 s
    (`MOVE_FOLLOW_SUPPRESS_MS`).
  - If the backend returns a different (normalized) `res.cwd`, that form is stored too.
  - The entry is deleted the first time it is checked (one-shot). This covers the echo arriving during
    the RPC and after it.
- **Gateway routing (from 0025, used by 0030).**
  - `moveTargetGateway` finds the session row in `$sessions` or the project tree and reads its
    `connection_id`.
  - A different connection is "remote", and its profile is resolved from `$connectionsRegistry`.
  - `moveTargetRequest` sends to `requestGatewayForAgent(...)` for remote targets and to
    `gatewayRequest` for local ones.
  - `createProjectFolderOn` (split out of 0025/0028's `ensureProjectRootOn` by 0030) runs `mkdir -p` and
    then `projects.create`. It also expires the remote tree cache, or calls `refreshProjects()` for a
    local gateway.

## Code

### `apps/desktop/src/lib/keybinds/actions.ts` (0018)

```ts
  // ⌘⇧P — "p" for project: re-home the selected session into another project
  // through a typeahead palette page (same move as the row menu's submenu).
  { id: 'session.moveToProject', category: 'session', defaults: ['mod+shift+p'] },
```

### `apps/desktop/src/app/hooks/use-keybinds.ts` (0018)

```ts
    'session.moveToProject': () => {
      const sessionId = $selectedStoredSessionId.get() || $focusedStoredSessionId.get()
      if (!sessionId) {
        return false
      }
      const profile = $sessions.get().find(s => sessionMatchesStoredId(s, sessionId))?.profile || undefined
      openMoveToProjectPalette({ profile, sessionId })
    },
```

### `apps/desktop/src/store/command-palette.ts` (0018)

```ts
export interface MoveToProjectTarget {
  profile?: string
  sessionId: string
}
export const $moveToProjectTarget = atom<MoveToProjectTarget | null>(null)

export function openMoveToProjectPalette(target: MoveToProjectTarget): void {
  $moveToProjectTarget.set(target)
  openCommandPalettePage('move-to-project')
}
// …and in setOpen(false):   $moveToProjectTarget.set(null)
```

### `apps/desktop/src/app/command-palette/index.tsx` (0018 + 0030)

```tsx
const STANDALONE_PAGES = new Set(['move-to-project'])   // Esc/Back closes instead of popping to root

const moveTarget = useStore($moveToProjectTarget)
useEffect(() => {
  if (page === 'move-to-project') void refreshProjectTree()
}, [page])

const movePage = useMemo<PalettePage>(() => {
  const p = t.sidebar.projects
  const target = moveTarget
  const session = target ? liveSessions.find(s => sessionMatchesStoredId(s, target.sessionId)) : undefined
  const title = session ? sessionTitle(session) : p.moveToProject
  // 0030: typed a name no project has → offer to create it and move there
  const typed = search.trim()
  const typedKey = typed.toLowerCase()
  const typedIsNew = Boolean(typed) && !projectTree.some(node => node.label.trim().toLowerCase() === typedKey)
  const createItems: PaletteItem[] = target && typedIsNew
    ? [{
        icon: Plus,
        id: 'move-to-project-create',
        keywords: ['create', 'new', 'project', typed],
        label: p.createAndMove(typed),
        run: () => {
          createProjectAndMoveSession(target.sessionId, typed, target.profile)
            .then(() => notify({ durationMs: 2_000, kind: 'success', message: p.movedTo(typed) }))
            .catch(err => notifyError(err, p.moveFailed))
        }
      }]
    : []

  return {
    title: p.moveToProject,
    placeholder: p.movePlaceholder(title),
    groups: target
      ? [
          {
            heading: t.commandCenter.projects,
            items: moveToProjectTargets(projectTree, liveSessions, target.sessionId).map(node => ({
              icon: codiconIcon(node.icon || 'folder-library'),
              id: `move-to-project-${node.id}`,
              keywords: ['project', 'move', node.label, ...(node.path ? [node.path] : [])],
              label: node.label,
              run: () => {
                moveSessionToProject(target.sessionId, node.id, target.profile)
                  .then(() => notify({ durationMs: 2_000, kind: 'success', message: p.movedTo(node.label) }))
                  .catch(err => notifyError(err, p.moveFailed))
              }
            }))
          },
          ...(createItems.length ? [{ items: createItems }] : [])
        ]
      : []
  }
}, [liveSessions, moveTarget, projectTree, search, t])

// back handler (deps become [page]):
if (page && STANDALONE_PAGES.has(page)) {
  closeCommandPalette()
  return
}
// page map: 'move-to-project': movePage   (and add movePage to that useMemo's deps)
```

### `apps/desktop/src/store/projects.ts`: follow suppression (0004)

```ts
const MOVE_FOLLOW_SUPPRESS_MS = 15_000
const moveFollowSuppressed = new Map<string, number>()

function suppressMoveFollow(cwd: string): void {
  const target = cwd.trim()
  if (target) moveFollowSuppressed.set(target, Date.now() + MOVE_FOLLOW_SUPPRESS_MS)
}

function consumeMoveFollowSuppression(cwd: string): boolean {
  const until = moveFollowSuppressed.get(cwd)
  if (until === undefined) return false
  moveFollowSuppressed.delete(cwd)
  return until >= Date.now()
}

export async function followActiveSessionCwd(cwd: string): Promise<void> {
  const target = cwd.trim()
  if (!target || consumeMoveFollowSuppression(target)) {
    return
  }
  // …upstream follow logic
```

### `apps/desktop/src/store/projects.ts`: targets (0018), create + move (0030)

```ts
export function moveToProjectTargets(
  tree: readonly SidebarProjectTree[],
  sessions: readonly SessionInfo[],
  sessionId: string
): SidebarProjectTree[] {
  const session = sessions.find(s => sessionMatchesStoredId(s, sessionId))
  const cwd = session?.cwd?.trim() || ''
  const currentProjectId = cwd ? projectIdForCwd(cwd) : null
  return tree.filter(node => node.id !== currentProjectId && !node.isNoProject && Boolean(projectRootCwd(node)))
}

export function newProjectFolder(name: string): string {
  const slug =
    name.trim().toLowerCase()
      .replace(/[^\p{L}\p{N}._-]+/gu, '-')
      .replace(/^[-.]+|-+$/g, '') || 'project'
  return `~/code/${slug}`
}

export async function createProjectAndMoveSession(
  sessionId: string, name: string, profile?: null | string, connectionId?: null | string
): Promise<void> {
  const label = name.trim()
  if (!label) throw new Error(translateNow('sidebar.projects.moveNoFolder'))
  const target = moveTargetGateway(sessionId, profile, connectionId)
  const projectProfile = target.profile || (target.remote ? 'default' : writableProjectProfile())
  const listed = await moveTargetRequest<ProjectsPayload>(target, 'projects.list', { profile: projectProfile })
  const existing = (listed.projects ?? []).find(
    p => !p.archived && (labelKey(p.name) === labelKey(label) || labelKey(p.slug) === labelKey(label))
  )
  const cwd =
    existing?.primary_path ||
    existing?.folders?.[0]?.path ||
    (await createProjectFolderOn(target, label, newProjectFolder(label), projectProfile))
  await moveSessionToCwd(target, sessionId, cwd)
}

async function createProjectFolderOn(target: MoveTarget, name: string, path: string, profile: string): Promise<string> {
  const make = await moveTargetRequest<ShellResult>(target, 'shell.exec', { command: `mkdir -p ${shellPath(path)}` })
  if (make.code !== 0) {
    throw new Error(`Could not create ${path} on ${target.connectionId}: ${(make.stderr || make.stdout).trim()}`)
  }
  const created = await moveTargetRequest<{ project: null | ProjectInfo }>(target, 'projects.create', {
    name, primary_path: path, profile
  })
  if (target.remote) expireRemoteProjectTree(target.connectionId)
  else void refreshProjects()
  return created.project?.primary_path || path
}

// moveSessionToProject(...) now resolves the node's root cwd, then: await moveSessionToCwd(target, sessionId, cwd)
async function moveSessionToCwd(target: MoveTarget, sessionId: string, cwd: string): Promise<void> {
  suppressMoveFollow(cwd)
  const res = await moveTargetRequest<WorkspaceMovePayload>(target, 'session.workspace.move', {
    cwd, session_key: sessionId, ...(target.profile ? { profile: target.profile } : {})
  })
  if (target.remote) expireRemoteProjectTree(target.connectionId)
  const moved = res.cwd || cwd
  if (moved !== cwd) suppressMoveFollow(moved)   // backend normalized the path
  setSessions(prev => prev.map(s => sessionMatchesStoredId(s, sessionId)
    ? { ...s, cwd: moved, git_branch: res.branch ?? null, git_repo_root: res.git_repo_root ?? null } : s))
  void refreshProjectTree()
}
```

`shellPath('~/code/x')` (from 0025) produces `"$HOME"/'code/x'`, so `~` expands on the remote host
and the rest is single-quoted.

### i18n

| Key | Patch | Added to | Listed in `overlay-gaps.json` for |
|---|---|---|---|
| `keybinds.actions['session.moveToProject']` | 0018 | `en.ts`, `de.ts`, `es.ts`, `fr.ts`, `ru.ts`, `zh.ts`, `ar_chrome.ts` | `ja`, `zh-hant` |
| `sidebar.projects.movePlaceholder: (title) => string` | 0018 | `types_projects.ts` and `{en,de,es,fr,ru,zh}_projects.ts` | `ar`, `ja`, `zh-hant` |
| `sidebar.projects.createAndMove: (name) => string` | 0030 | same files as `movePlaceholder` | `ar`, `ja`, `zh-hant` |

0018 also changes `app/chat/sidebar/session-actions-menu.tsx`: its `MoveToProjectItems` now calls
`moveToProjectTargets` instead of the filter it had inline. The test mock in
`session-actions-menu.test.tsx` gains `moveToProjectTargets: vi.fn(() => [])`.

## Tests

| File | Patch | What it covers |
|---|---|---|
| `src/store/projects.test.ts` | 0004 | `moveSessionToProject keeps the sidebar scope`: a control test (a live `followActiveSessionCwd` still enters the project), plus no follow when `session.info` arrives during the move RPC or after it. 0004 also sets `$sidebarShowAllSessions` to `false` in a top-level `beforeEach` so existing RPC assertions keep the capped `preview_limit`. |
| `src/store/projects.test.ts` | 0018 | `moveToProjectTargets` leaves out the owner and Home, and lists all foldered projects for a session outside any project. |
| `src/store/projects-cross-gateway-move.test.ts` | 0030 | `createProjectAndMoveSession`: call order `projects.list` → `shell.exec` (`mkdir -p "$HOME"/'code/my-thing'`) → `projects.create` → `session.workspace.move`; reuse of a same-named project (no `shell.exec`/`create`); slug cases. The file comes from 0025. |

Run from `apps/desktop` (vitest config in `vitest.config.ts`; `npm test` = `vitest run`):

```sh
cd apps/desktop
npx vitest run src/store/projects.test.ts src/store/projects-cross-gateway-move.test.ts \
  src/app/chat/sidebar/session-actions-menu.test.tsx
npx vitest run src/i18n src/lib/keybinds   # overlay-gaps + keybind registry checks
npx tsc -p . --noEmit
```

Verified on current `upstream/main`:

- 0004 + 0018 alone: 70/70 tests pass, `tsc` is clean.
- 0004, 0018, 0024, 0025, 0028, 0030: 80/80 tests pass. The i18n and keybinds suites pass too.

## Applying

Order and dependencies (checked with `git am -3` on a clean `upstream/main` checkout):

| Patch | Needs | Why |
|---|---|---|
| 0004 | nothing | |
| 0018 | 0004 | Textual: it does not apply without 0004. |
| 0030 | 0025 and 0028 | It edits code those patches add: `moveTargetGateway`, `moveTargetRequest`, `MoveTarget`, `ShellResult`, `shellPath`, `labelKey`, `ensureProjectRootOn`, `projects-cross-gateway-move.test.ts`. 0028 is needed because the hunk context is its `mkdir -p` version of `ensureProjectRootOn`. Without them, `git am` fails. |
| 0025 | 0024 at runtime | 0025 applies on top of 0004 + 0018 without 0024, but its code calls `ownerGatewayId` / `isRemoteProjectId` from 0024. Without 0024, the projects tests fail with `ReferenceError`. |

Minimal sets:

```sh
cd ~/.hermes/hermes-agent        # or your hermes-agent checkout
M=~/code/hermes-mods/core/patches

# ⌘⇧P move only (no "Create project"):
git am -3 $M/0004-*.patch $M/0018-*.patch

# full feature, including Create project:
git am -3 $M/0004-*.patch $M/0018-*.patch $M/0024-*.patch $M/0025-*.patch $M/0028-*.patch $M/0030-*.patch
```

Applying the whole series in order also works. The only patch that conflicts on upstream is 0013,
which is unrelated to this feature.

To re-implement against a newer upstream without 0024/0025/0028:

- Write `createProjectAndMoveSession` against the active gateway only: `gatewayRequest` in place of
  `moveTargetRequest`, with `target = { remote: false }`.
- Copy `shellPath` and `labelKey` from the excerpts above.

## Gotchas

- **Rebuild the desktop app.** Renderer changes don't show up until you rebuild:
  `hermes desktop --build-only --force-build`. On macOS, set `APPLE_SIGNING_IDENTITY`; otherwise the
  build is ad-hoc signed.
- **No generated files.** No RPC contract changes, so you don't need to regenerate
  `gateway-contract.generated.ts` or `openrpc.json`. `npm run i18n:keys` rewrites
  `locales/_keys.desktop.json`; don't commit that churn unless you mean to.
- **i18n ratchet.** `src/i18n/overlay-completeness.test.ts` fails when a locale overlay lacks a new key
  that isn't listed in `overlay-gaps.json`. Either add the key to every overlay or list the missing
  locales, as these patches do. `types_projects.ts` must also get the key, or `tsc` fails for every
  `*_projects.ts`.
- **Keybind registry.** To add a hotkey, add a row in `lib/keybinds/actions.ts` and a handler in
  `use-keybinds.ts`; nothing else is needed. `actions.test.ts` requires unique ids and an English label
  (`en.ts` `keybinds.actions`).
  - Upstream ships nothing on `mod+shift+p`.
  - Some plugins rebind chords. Check that no installed plugin claims ⌘⇧P.
- **The "is it new" check is local.**
  - The palette compares the typed text only against labels in the visible `$projectTree`, which
    includes the Home bucket's label. A name that matches any visible label, even on another gateway,
    hides **Create**.
  - A name that only exists on the session's own gateway still shows **Create**. The gateway-side
    `projects.list` check then reuses that project, so no duplicate is created.
- **`shell.exec` safety gate.** `shell.exec` runs commands through `tools.approval_detection`. A plain
  `mkdir -p` passes. If that module can't be imported on the target gateway, the create fails with
  `5001 shell.exec unavailable`. The command times out after 30 s.
- **Folderless projects can't be targets.** They are filtered out of the list. The row menu shows
  `moveNoFolder` for them.
- **Suppression is keyed by exact cwd string.** If the backend's echoed cwd differs from both the
  requested path and `res.cwd` (for example a symlink resolved differently), the sidebar will still
  follow.

## Could this be a plugin instead?

Partly, but not the whole feature.

What a desktop plugin (`@hermes/plugin-sdk`) can do:

- Register a `KEYBINDS_AREA` action on `mod+shift+p` and a `PALETTE_AREA` root row, as
  `desktop-plugins/spin-out/plugin.js` does.
- Call `host.request('projects.list' | 'projects.create' | 'shell.exec' | 'session.workspace.move', …)`
  on the active gateway.
- Read the selected/focused session through the host.

What it can't do:

- **No palette sub-page.** `PaletteContribution` is a flat row (`label`, `run`, `keywords`). A plugin
  can't open a typeahead page inside the palette or add a dynamic "Create …" row that depends on the
  search text. It would have to render its own picker, e.g. a modal.
- **Can't stop the sidebar following.** `followActiveSessionCwd` and `$projectScope` are internal to
  `store/projects.ts`. A plugin-driven move would still switch the sidebar into the destination, so
  0004 has to stay in core.
- **Cross-gateway routing is internal.** `moveTargetGateway` and `requestGatewayForAgent` are not
  exported to plugins. The SDK does have `requestProfile` / `profileRoutes`, which might be enough for
  remote targets; that hasn't been tried.

Verdict: 0004 must stay a core patch. 0018 and 0030 could be rebuilt as a plugin with its own picker UI,
but it would lose the built-in palette UX and wouldn't share code with the row menu's "Move to project".
