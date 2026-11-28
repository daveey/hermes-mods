// Extract and SANITIZE real assistant replies into a structure-preserving
// corpus (the real_replies of ../desktop-oracle.json were produced by this
// script; only its sanitized output is ever committed). Every letter word is
// replaced by a deterministic placeholder of the same capitalization except a
// small structural allowlist; digits become 0; URLs, emails, paths and hex ids
// are replaced by fixed safe values.
//   sanitize-real.mjs <out.json>
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
const SESSIONS = `${process.env.HOME}/.hermes/sessions`
const ATT = `${process.env.HOME}/.hermes/kanban/attachments`
const files = readdirSync(SESSIONS).filter(f => /^request_dump_.*\.json$/.test(f))
const KEEP = new Set(['plan', 'Plan', 'PLAN', 'the', 'and', 'to', 'a', 'of', 'in', 'is', 'it', 'for', 'on', 'with', 'step', 'steps', 'next', 'run', 'test', 'tests', 'fix', 'check'])
const POOL = ['alpha', 'bravo', 'delta', 'echo', 'kilo', 'lima', 'oscar', 'papa', 'sierra', 'tango', 'victor', 'zulu', 'amber', 'cobalt', 'ember', 'flint']
function word(w) {
  if (KEEP.has(w)) return w
  const h = createHash('sha256').update(w.toLowerCase()).digest()[0]
  let out = POOL[h % POOL.length]
  if (/^[A-Z]/.test(w)) out = out[0].toUpperCase() + out.slice(1)
  if (w.length > 2 && w === w.toUpperCase()) out = out.toUpperCase()
  return out
}
const URL_OR_MAIL = /(https?:\/\/[^\s)>\]"'`]+|www\.[\w-]+\.[^\s)>\]"'`]+|[\w.+-]+@[\w-]+\.[\w.-]+)/g
function sanitizeText(text) {
  return text
    .replace(/(?:~|\/)[\w.-]*(?:\/[\w.@-]+)+/g, '/path/to/file')
    .replace(/\b[0-9a-f]{7,64}\b/g, 'abc1234')
    .replace(/[0-9]/g, '0')
    .replace(/[\p{L}][\p{L}'’]*/gu, word)
}
// URLs and emails keep their autolink-relevant shape with fixed safe values.
function sanitize(text) {
  return text.split(URL_OR_MAIL).map((part, i) => i % 2 === 0 ? sanitizeText(part)
    : part.includes('@') && !/^https?:|^www\./.test(part) ? 'user@example.com'
    : part.startsWith('www.') ? 'www.example.com/a_b' : 'https://example.com/a_b?x=0&y=0').join('')
}
const seen = new Set(), out = []
function texts(content) {
  if (typeof content === 'string') return [content]
  if (Array.isArray(content)) return content.filter(p => p && p.type === 'text' && typeof p.text === 'string').map(p => p.text)
  return []
}
function keep(t) {
  const s = sanitize(t)
  const k = createHash('sha256').update(s).digest('hex')
  if (!seen.has(k)) { seen.add(k); out.push(s) }
}
function walk(v) {
  if (!v || typeof v !== 'object') return
  if (Array.isArray(v)) { v.forEach(walk); return }
  if (v.role === 'assistant') for (const t of texts(v.content)) if (t.trim().length >= 40 && t.length <= 20000) keep(t)
  for (const x of Object.values(v)) walk(x)
}
// Finished assistant messages in local request dumps.
for (const f of files) {
  try { walk(JSON.parse(readFileSync(`${SESSIONS}/${f}`, 'utf8'))) } catch (e) { console.error('skip', f, String(e).slice(0, 80)) }
}
// Agent-authored final Markdown reports (Kanban attachments): deterministic
// sample of every 13th file by sorted path, at most 4000 characters.
const mds = execFileSync('find', [ATT, '-name', '*.md', '-size', '-20k'], { encoding: 'utf8' }).split('\n').filter(Boolean).sort()
for (let i = 0; i < mds.length; i += 13) {
  const t = readFileSync(mds[i], 'utf8')
  if (t.trim().length >= 40 && t.length <= 4000) keep(t)
}
out.sort()
writeFileSync(process.argv[2], JSON.stringify({ description: 'Sanitized structure-only assistant replies (all words replaced, digits zeroed, URLs/emails/paths/ids replaced)', count: out.length, replies: out }, null, 1) + '\n')
console.log('sanitized replies', out.length, 'from', files.length, 'dumps')
