// Regression: Desktop answers a connection-pinned /api/profiles/sessions?profile=all
// with the ALL-gateways aggregate once the pinned gateway is pooled. Rows must be
// attributed by their `connection_id` (absent = primary), never by the requesting
// connection, and the same aggregate from three pinned requests must collapse to
// one row per (gateway, profile, id).
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(process.env.HOME + '/.hermes/desktop-plugins/session-mentions/plugin.js', 'utf8')
Error.stackTraceLimit = 1
async function load(host) {
 globalThis.__smHost = host
 const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
 return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}
const conns = [
 { id: 'local', label: 'This device', key: 'this-device', primary: true },
 { id: 'skint-polycentrism', label: 'nous-1', key: 'nous-1', primary: false },
 { id: 'mac-mini', label: 'mac-mini', key: 'mac-mini', primary: false }
]
const aggregate = [
 { id: 'loc1', title: 'local fly brain', profile: 'default', last_active: 100 },
 { id: 'rem1', title: 'remote job', profile: 'default', last_active: 90, connection_id: 'skint-polycentrism' },
 { id: 'mm1', title: 'mini job', profile: 'work', last_active: 80, connection_id: 'mac-mini' }
]
test('rows are owned by connection_id, untagged rows belong to the primary', async () => {
 const m = await load({ state: {} })
 const rows = m.tagRows(aggregate, conns[1], conns) // asked nous-1, got the aggregate
 assert.deepEqual(rows.map(r => [r.id, r._conn, r._gwKey]), [
  ['loc1', 'local', 'this-device'],
  ['rem1', 'skint-polycentrism', 'nous-1'],
  ['mm1', 'mac-mini', 'mac-mini']
 ])
})
test('the same aggregate from every pinned request collapses to one row each', async () => {
 const m = await load({ state: {} })
 const all = conns.flatMap(c => m.tagRows(aggregate, c, conns))
 assert.equal(all.length, 9)
 const rows = m.dedupeRows(all)
 assert.deepEqual(rows.map(r => `${r._conn}/${r.id}`).sort(), ['local/loc1', 'mac-mini/mm1', 'skint-polycentrism/rem1'])
})
test('typeahead inserts gateway-qualified refs only for sessions that live elsewhere', async () => {
 const m = await load({ state: {} })
 const rows = m.dedupeRows(conns.flatMap(c => m.tagRows(aggregate, c, conns)))
 const items = m.sessionItems(rows, 'session:', { active: 'local', conns })
 assert.deepEqual(items.map(i => i.insert), [
  '@session:default/loc1',
  '@session:default@nous-1/rem1',
  '@session:work@mac-mini/mm1'
 ])
})
test('a direct (non-aggregate) pinned answer tagged by Desktop keeps the pinned gateway', async () => {
 const m = await load({ state: {} })
 const rows = m.tagRows([{ id: 'r9', profile: 'default', connection_id: 'skint-polycentrism' }], conns[1], conns)
 assert.equal(rows[0]._conn, 'skint-polycentrism')
})
