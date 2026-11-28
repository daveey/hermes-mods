// Compare a fresh oracle run with the verdicts recorded in ../desktop-oracle.json.
// check-oracle.mjs <oracle-out.json>
import { readFileSync } from 'node:fs'
const f = JSON.parse(readFileSync(new URL('../desktop-oracle.json', import.meta.url), 'utf8'))
const recorded = new Map(f.synthetic.map(c => [c.id, c.desktop]))
for (const [id, v] of Object.entries(f.real)) recorded.set(id, v.desktop)
for (const [id, v] of Object.entries(f.fuzz)) recorded.set(id, v.desktop)
const fresh = JSON.parse(readFileSync(process.argv[2], 'utf8'))
let diff = 0
for (const r of fresh) {
  const want = recorded.get(r.id)
  if (r.error || !want || JSON.stringify([r.cards, r.card_last]) !== JSON.stringify([want.cards, want.card_last])) { diff++; console.log('DIFF', r.id, r.error ?? '') }
}
if (fresh.length !== recorded.size) { diff++; console.log('COUNT', fresh.length, recorded.size) }
console.log(`oracle cases=${fresh.length} differing=${diff}`)
process.exit(diff ? 1 : 0)
