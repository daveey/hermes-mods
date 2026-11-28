// Export every case of ../desktop-oracle.json as the oracle's input list.
// cases-from-fixture.mjs <out.json>
import { readFileSync, writeFileSync } from 'node:fs'
import fuzz from './fuzz.mjs'
const f = JSON.parse(readFileSync(new URL('../desktop-oracle.json', import.meta.url), 'utf8'))
const cases = f.synthetic.map(c => ({ id: c.id, reply: c.reply }))
f.real_replies.forEach((reply, i) => {
  for (const [v, suffix] of Object.entries(f.real_variants)) {
    cases.push({ id: `real/${String(i).padStart(3, '0')}/${v}`, reply: v === 'plain' ? reply : reply.trimEnd() + suffix })
  }
})
for (const c of fuzz) cases.push({ id: c.id, reply: c.reply })
writeFileSync(process.argv[2], JSON.stringify(cases))
console.log('cases', cases.length)
