// The courier's `authority_read`: a hub Kanban worker's coach reads its card's origin chat on another gateway
// (coach origin_authority.py) to find the user's typed "approved" to a priced ask. The read pages the whole
// display transcript (an approval can sit thousands of rows back) and keeps only the rows that check reads.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'
const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
async function load(api) {
  globalThis.__smHost = { state: { connectionId: { get: () => 'local' }, profile: { get: () => 'default' } }, connections: async () => [] }
  globalThis.window = { hermesDesktop: { api } }
  const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = globalThis.__smHost; const COMPOSER_AREAS = {}')
  return import('data:text/javascript;base64,' + Buffer.from(code + '\n//' + Math.random()).toString('base64'))
}

test('authorityRows keeps each user row and only the last assistant text before it', async () => {
  const m = await load(async () => ({}))
  const rows = m.authorityRows([
    { id: 1, role: 'assistant', content: 'working…' },
    { id: 2, role: 'assistant', content: 'Approve or decline $36 total?' },
    { id: 3, role: 'assistant', content: '', tool_calls: [{}] },
    { id: 4, role: 'tool', content: 'x' },
    { id: 5, role: 'user', content: 'approved', display_kind: '', _queued: 1, reasoning: 'r' },
    { id: 6, role: 'user', content: [{ type: 'text', text: 'and' }, { type: 'image_url' }, { type: 'text', text: 'more' }] }
  ])
  assert.deepEqual(rows.map(r => r.id), [2, 5, 6])
  assert.equal(rows[1]._queued, true)
  assert.equal(rows[1].reasoning, undefined)
  assert.equal(rows[2].content, 'and more')
  const long = m.slimAuthorityRow({ id: 7, role: 'assistant', content: '[CONTEXT COMPACTION' + 'x'.repeat(9000) + ' approve $36?' })
  assert.ok(long.content.startsWith('[CONTEXT COMPACTION') && long.content.endsWith(' approve $36?'))
  assert.ok(long.content.length < 6400)
})

test('readAuthorityTranscript pages the whole transcript oldest-first with compaction history', async () => {
  const paths = []
  const all = Array.from({ length: 1203 }, (_, i) => ({ id: i, role: i % 2 ? 'user' : 'assistant', content: `m${i}` }))
  const m = await load(async () => ({}))
  const res = await m.readAuthorityTranscript({ connectionId: 'macbook', profile: 'default', sessionId: 's1' }, async (conn, path) => {
    paths.push(path)
    assert.equal(conn, 'macbook')
    const off = Number(new URLSearchParams(path.split('?')[1]).get('offset'))
    return { session_id: 's1', profile: 'default', messages: all.slice(off, off + m.AUTHORITY_PAGE) }
  })
  assert.equal(res.ok, true)
  assert.equal(res.total, 1203)
  assert.equal(paths.length, 3)
  assert.ok(paths.every(p => p.includes('order=oldest') && p.includes('include_compacted=true') && p.includes('profile=default')))
  assert.equal(res.messages.length, 1203 - 1) // trailing assistant row has no user reply after it
})

test('a transcript past the page cap is an error, never a partial read', async () => {
  const m = await load(async () => ({}))
  const page = Array.from({ length: 500 }, (_, i) => ({ id: i, role: 'user', content: 'x' }))
  const res = await m.readAuthorityTranscript({ connectionId: 'c', sessionId: 's' }, async () => ({ messages: page }))
  assert.equal(res.ok, false)
  assert.match(res.error, /longer than/)
})

test('performRequest routes authority_read to the target gateway', async () => {
  const calls = []
  const m = await load(async req => {
    calls.push(req)
    return { session_id: 's9', messages: [{ id: 1, role: 'assistant', content: 'Approve $36?' }, { id: 2, role: 'user', content: 'approved' }] }
  })
  const conns = [{ id: 'local', key: 'hub', kind: 'local' }, { id: 'mb', key: 'macbook', label: 'macbook', kind: 'ssh' }]
  const res = await m.performRequest({ action: 'authority_read', args: { target: '@session:default@macbook/s9' } }, 'local', conns)
  assert.equal(res.ok, true)
  assert.deepEqual(res.messages.map(r => r.content), ['Approve $36?', 'approved'])
  assert.equal(calls[0].connectionId, 'mb')
})
