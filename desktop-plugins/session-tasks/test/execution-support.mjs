// Shared loader for the execution-observation consumer tests (session-tasks and session-panel).
// Verdicts are NOT written here: the golden cases come from the core's frozen fixture
// (schemas/fixtures/execution-observations-v1.json), read from HERMES_CORE_SOURCE (or an explicit `core`
// argument) when set, else from the byte-identical public copy below, whose sha256 is pinned. The projector is
// the core's own pure module, imported by path, so these tests exercise the same function a host publishes
// through the plugin SDK. Without a core checkout the projector-backed tests skip visibly; hash and
// missing-SDK tests still run.
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
export const PUBLIC_FIXTURE = resolve(here, 'fixtures/execution-observations-v1.json')
export const PUBLIC_FIXTURE_SHA256 = 'a0f25162f8579d25c298d3313f5d6903a1a4da841c3cec47ac7cf74d572bc4de'
const CORE_FIXTURE = 'schemas/fixtures/execution-observations-v1.json'
const CORE_PROJECTOR = 'apps/desktop/src/lib/execution-observations.ts'

export const sha256 = bytes => createHash('sha256').update(bytes).digest('hex')
export const coreSource = core => core || process.env.HERMES_CORE_SOURCE || null

export function loadFixture(core) {
  const root = coreSource(core)
  const path = root && existsSync(resolve(root, CORE_FIXTURE)) ? resolve(root, CORE_FIXTURE) : PUBLIC_FIXTURE
  const bytes = readFileSync(path)
  return { fixture: JSON.parse(bytes.toString('utf8')), path, sha256: sha256(bytes), fromCore: path !== PUBLIC_FIXTURE }
}

/** The core's pure projector module (TypeScript, type-stripped by Node), or null without a core checkout. */
export async function loadProjector(core) {
  const root = coreSource(core)
  const path = root && resolve(root, CORE_PROJECTOR)
  if (!path || !existsSync(path)) return null
  return import(pathToFileURL(path).href)
}

export const clone = v => structuredClone(v)

export function apply(value, patch) {
  for (const [path, replacement] of Object.entries(patch.set ?? {})) {
    const keys = path.split('.')
    let parent = value
    for (const key of keys.slice(0, -1)) parent = parent[key]
    parent[keys.at(-1)] = clone(replacement)
  }
  for (const path of patch.delete ?? []) {
    const keys = path.split('.')
    let parent = value
    for (const key of keys.slice(0, -1)) parent = parent[key]
    delete parent[keys.at(-1)]
  }
  return value
}

export const caseInput = (fixture, patch) => apply(clone(fixture.base), patch)
export const itemInput = (fixture, item) => ('raw' in item ? clone(item.raw) : caseInput(fixture, item))

/** Install the SDK stub the plugins import: real projector exports when `projector` is given, else none (an old host). */
export const sdkStubSource = (extra = '') => `const S=globalThis.__obsSdk||{};export const projectExecutionObservation=S.projectExecutionObservation,selectExecutionObservation=S.selectExecutionObservation,summarizeExecutionObservations=S.summarizeExecutionObservations,executionIdentityKey=S.executionIdentityKey,COMPOSER_AREAS={top:"composer.top"},host=globalThis.__obsHost||{},useValue=a=>a?.get?.(),Codicon=()=>null,DisclosureCaret=()=>null;${extra}`
