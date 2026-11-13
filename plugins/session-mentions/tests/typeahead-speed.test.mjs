// The @ popover scores every cached session row (tens of thousands across gateways) on each
// keystroke. sessionItems() picks the top matches with topMatches() instead of sorting every hit,
// and score() caches each row's tokenized fields. These tests pin that the fast path picks exactly
// what the old map/filter/sort pipeline picked.
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { test } from 'node:test'

const source = await fs.readFile(new URL('../../../desktop-plugins/session-mentions/plugin.js', import.meta.url), 'utf8')
const code = source.replace("import { COMPOSER_AREAS, host } from '@hermes/plugin-sdk'", 'const host = { state: {} }; const COMPOSER_AREAS = {}')
const m = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'))

let seed = 7
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647)
const words = 'baby bear hub fix desktop typeahead slow session kanban card release mini'.split(' ')
const phrase = n => Array.from({ length: n }, () => words[Math.floor(rand() * words.length)]).join(rand() < 0.5 ? ' ' : '-')
const rows = Array.from({ length: 3000 }, (_, i) => ({
  id: `2026_${i % 7}_${i}`,
  title: rand() < 0.1 ? '' : phrase(4),
  preview: phrase(10),
  profile: 'default',
  last_active: Math.floor(rand() * 40),
  _conn: `c${i % 3}`,
  _gwLabel: `gw-${i % 3} mini`
}))

const recency = (a, b) => Number(b.last_active || 0) - Number(a.last_active || 0)
const naive = (list, scoreOf, limit) =>
  list
    .map(row => ({ row, s: scoreOf(row) }))
    .filter(x => x.s > 0)
    .sort((a, b) => b.s - a.s || recency(a.row, b.row))
    .slice(0, limit)
    .map(x => x.row)

test('topMatches picks the same rows, in the same order, as a full stable sort', () => {
  for (const q of ['ba', 'bab', 'baby b', 'ty', 'ahead', 'gw', 'mini', '2026_3', 'card rel', 'zz']) {
    for (const limit of [1, 8, 50]) {
      const scoreOf = row => m.score(row, q)
      assert.deepEqual(m.topMatches(rows, scoreOf, recency, limit).map(r => r.id), naive(rows, scoreOf, limit).map(r => r.id), `${q} / ${limit}`)
    }
  }
})

test('cached row fields do not change scores: a multi-word query still ignores the preview', () => {
  const row = { id: 'x1', title: 'release notes', preview: 'baby bear hub', _gwLabel: 'hub' }
  assert.ok(m.score(row, 'baby') > 0) // preview hit, single word
  const items = m.sessionItems([{ ...row, _conn: 'local' }], 'baby bear', { active: 'local', conns: [{ id: 'local' }] })
  assert.equal(items.filter(i => i.icon === 'session' && i.insert.includes('x1')).length, 0)
})

test('sessionItems on 30k rows stays well inside a keystroke budget once warm', () => {
  const big = Array.from({ length: 30_000 }, (_, i) => ({ ...rows[i % rows.length], id: `big_${i}` }))
  const conns = [{ id: 'c0' }, { id: 'c1' }, { id: 'c2' }]
  m.sessionItems(big, 'zz', { active: 'c0', conns }) // warm the per-row cache
  // Best of 5 rounds: CI runs this suite beside 3 others (-j4), and one starved round read 63.7 ms
  // while this path takes ~10-18 ms unloaded. A real regression is slow in every round.
  const rounds = []
  for (let r = 0; r < 5; r++) {
    const t = performance.now()
    for (const q of ['ty', 'typ', 'typea', 'typeahead']) m.sessionItems(big, q, { active: 'c0', conns })
    rounds.push((performance.now() - t) / 4)
  }
  const perKey = Math.min(...rounds)
  assert.ok(perKey < 60, `${perKey.toFixed(1)} ms per keystroke (rounds: ${rounds.map(x => x.toFixed(1)).join(', ')})`)
})
