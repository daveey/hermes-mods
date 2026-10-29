# fix(desktop): keep remote clients off background local spawns [daveey] (#149)

Patch 0745, source `4c17b356e9d72009dfcdb7693b35e26e34ddf10f`.

## Behavior

fix(desktop): keep remote clients off background local spawns [daveey] (#149). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `import { describe, expect, it, vi } from 'vitest'`
- `+import { backendScopeKey } from './connection-registry'`
- `+import { expect, test } from 'vitest'`
- `+export function createRegistryConnectionSanitizer(deps: {`
- `test('profile remote wins precedence and carries one exact registry id', () => {`
- `test('environment route wins over global but never claims a registry id', () =>`
- `export function resolveDesktopRemoteRoute({`
- `+import assert from 'node:assert/strict'`
- `+import { connectionIdForLabel, type ConnectionRegistry } from './connection-registry'`
- `import {`
- `import { decodeResponseBody, displayTranscriptHeaders } from './display-transcri`
- `function registerMediaProtocol(): void {`
- `function writeDesktopConnectionConfig(config) {`
- `function preserveCorruptRegistrySidecar() {`
- `function writeDesktopConnectionsRegistry(registry) {`
- `function sanitizeConnectionsRegistry(registry = readDesktopConnectionsRegistry()`
- `async function ensureTerminalBackend(webContentsId: number) {`
- `async function connectDesktopProfileRoute(`
- `function revalidatePool() {`
- `ipcMain.handle('hermes:plugin-profile-routes', async (_event, rawProfileNames) =`
- `ipcMain.handle('hermes:connections:save', async (_event, payload) => {`
- `async function enumerateRegistryAgentSources(registry = readDesktopConnectionsRe`
- `ipcMain.handle('hermes:connections:update-all', async (_event, payload) => {`
- `async function dispatchRegistryApiRequest(`
- `app.whenReady().then(() => {`
- `describe('plugin REST qualified owner', () => {`
- `export interface PluginRestOptions {`
- `export async function pluginRest<T>(pluginId: string, path: string, opts: Plugin`
- `afterEach(() => {`
- `describe('gateway names', () => {`
- `export async function hubGet<T extends object>(`
- `+import type { SidebarProjectTree } from '@/app/chat/sidebar/projects/workspace-groups'`
- `beforeEach(ctx => {`
- `export async function refreshProjects(): Promise<void> {`
- `function reapplyLastProjectTree(): void {`

## Code

- `apps/desktop/electron/backend-dial-claim.ts`
- `apps/desktop/electron/connection-registry-view.ts`
- `apps/desktop/electron/desktop-remote-route.ts`
- `apps/desktop/electron/environment-registry.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/src/api/plugins.ts`
- `apps/desktop/src/store/hub-sync.ts`
- `apps/desktop/src/store/project-tree-background.ts`
- `apps/desktop/src/store/projects.ts`

## Tests

Source test files changed by this commit:

- `apps/desktop/electron/backend-dial-claim.test.ts`
- `apps/desktop/electron/connection-registry-view.test.ts`
- `apps/desktop/electron/desktop-remote-route.test.ts`
- `apps/desktop/electron/environment-registry.test.ts`
- `apps/desktop/src/api/plugins.test.ts`
- `apps/desktop/src/store/hub-sync.test.ts`
- `apps/desktop/src/store/projects-hub-replica.test.ts`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
