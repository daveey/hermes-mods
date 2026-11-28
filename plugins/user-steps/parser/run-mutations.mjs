// Mutation testing of scanner and dependency-identity guards.
// run-mutations.mjs <parser dir> <work dir>
// Each mutant: copy the parser (real node_modules copy), apply ONE exact
// textual mutation (must match exactly once), rebuild when scanner.ts changed,
// run check-fidelity.mjs (+ smoke.mjs); a mutant survives if both exit 0.
import { cpSync, mkdirSync, readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { join } from 'node:path'
const [src, work] = process.argv.slice(2)
const S = 'scanner.ts', L = 'lockgraph.mjs'
const MUTANTS = [
  ['C0 harness control (no-op, must survive)', S, "const parse = (text:string)", "const parse = (text:string)"],
  ['S1 drop remark-math stage', S, ".use(REMARK_PLUGINS).use([mathPlugin.remarkPlugin])", ".use(REMARK_PLUGINS)"],
  ['S2 drop tail-repair refusal', S, "if(repaired!==text && repaired!==text.replace(/[ \\t\\r\\n]+$/,'')) return null", "void preprocessWithTailRepair"],
  ['S3 drop block reconstruction check', S, "if(!Array.isArray(split) || split.join('')!==text.replace(/\\r\\n|\\r/g,'\\n')) return null", "if(!Array.isArray(split)) return null"],
  ['S4 no CR normalization (over-strict)', S, "split.join('')!==text.replace(/\\r\\n|\\r/g,'\\n')", "split.join('')!==text"],
  ['S5 drop earlier-block doubt', S, " for(const block of content.slice(0,-1)) if(directives(parse(block)).doubt) return null\n", ""],
  ['S6 earlier-block check skips first block', S, "content.slice(0,-1)) if(directives", "content.slice(1,-1)) if(directives"],
  ['S8 drop last-block check', S, " if(!finalDirective(parse(content.at(-1) as string))) return null\n", ""],
  ['S9 last block parsed as whole text', S, "if(!finalDirective(parse(content.at(-1) as string)))", "if(!finalDirective(parse(text)))"],

  ['S11 reject harmless terminal whitespace removal', S, "repaired!==text && repaired!==text.replace(/[ \\t\\r\\n]+$/,'')", "repaired!==text"],
  ['H1 drop raw-html doubt', S, "if(n.type==='html' && !SAFE_HTML.test(n.value.trim())) doubt=true", ""],
  ['H2 no comment allowance', S, "(?:<!--(?:(?!-->)[\\s\\S])*-->|<(?:br", "(?:<(?:br"],
  ['H3 no void-element allowance', S, "-->|<(?:br|hr|img|wbr)\\b[^<>]*>)$", "-->)$"],
  ['H4 unanchored safe-html', S, "[^<>]*>)$/i", "[^<>]*>)/i"],
  ['L1 no version check', L, "if(meta.version!==version) errors.push", "if(false) errors.push"],
  ['L2 no integrity check', L, "if(!integrity || meta.integrity!==integrity) errors.push", "if(false) errors.push"],
  ['L3 missing integrity accepted', L, "if(!integrity || meta.integrity!==integrity)", "if(integrity && meta.integrity!==integrity)"],
  ['L4 absent edge accepted', L, "if(value===undefined) { errors.push(`${parentLabel} -> ${name}: edge absent from Desktop lock (${parentKey})`); return }", "if(value===undefined) return"],
  ['L5 external accepted', L, "if(!/^node:/.test(imp.path)) errors.push", "if(false) errors.push"],
  ['L6 unreached accepted', L, "for(const inst of bundled) if(!packages.has(inst)) errors.push", "for(const inst of bundled) if(false) errors.push"],
  ['L7 alias name unchecked', L, "if((meta.name ?? name)!==lockName) errors.push", "if(false) errors.push"],
  ['L8 shaken counted as shipped', L, "if(info.bytesInOutput>0 && instanceOf(file))", "if(instanceOf(file))"],
  ['L9 missing npm entry accepted', L, "if(!meta) { errors.push(`${child}: not in package-lock.json`); return }", "if(!meta) return"],
  ['L10 no importer accepted', L, "if(!importer) return {errors:[`Desktop lock has no importer ${IMPORTER}`],packages:[]}", "if(!importer) return {errors:[],packages:[]}"],
]
mkdirSync(work, { recursive: true })
const results = []
for (const [name, file, from, to] of MUTANTS) {
  // One reusable directory with a REAL node_modules copy: esbuild resolves
  // symlinks to real paths, which would break the lock-graph mapping.
  const dir = join(work, 'm')
  if (!existsSync(dir)) {
    mkdirSync(dir)
    for (const f of readdirSync(src)) cpSync(join(src, f), join(dir, f), { recursive: true, verbatimSymlinks: true })
  }
  for (const f of readdirSync(src)) if (f !== 'node_modules') cpSync(join(src, f), join(dir, f), { recursive: true })
  const text = readFileSync(join(dir, file), 'utf8')
  if (text.split(from).length !== 2) throw Error(`Mutation did not apply exactly once: ${name}`)
  writeFileSync(join(dir, file), text.replace(from, to))
  for (const f of ['scanner.mjs', 'manifest.json', 'THIRD-PARTY-LICENSES.txt']) writeFileSync(join(dir, f), readFileSync(join(src, f)))
  if (file === S) {
    const b = spawnSync(process.execPath, ['build.mjs'], { cwd: dir, encoding: 'utf8', env: { ...process.env, HERMES_CORE_COMMIT: 'a960c29ca05d8fb81d2ca0f99d2e1a146059bb19' } })
    if (b.status !== 0) throw Error(`Mutation build failed (not an assertion kill): ${name}\n${b.stderr}`)
  }
  const smoke = spawnSync(process.execPath, ['smoke.mjs'], { cwd: dir, encoding: 'utf8', timeout: 120000 })
  const fid = spawnSync(process.execPath, ['check-fidelity.mjs'], { cwd: dir, encoding: 'utf8', timeout: 600000 })
  const fails = (fid.stderr.match(/^FAIL .*/gm) ?? []).map(l => l.split(':')[0])
  const id = name.split(' ')[0]
  writeFileSync(join(work, `${id}.stdout.log`), smoke.stdout + fid.stdout)
  writeFileSync(join(work, `${id}.stderr.log`), smoke.stderr + fid.stderr)
  if (smoke.error || fid.error || fid.signal || smoke.signal) throw Error(`Harness error, not a kill: ${name}`)
  if (smoke.status !== 0 || (fid.status !== 0 && !fails.length)) throw Error(`Non-assertion failure, not a kill: ${name}`)
  const status = smoke.status === 0 && fid.status === 0 ? 'SURVIVED' : 'KILLED'
  results.push({ name, status, smoke: smoke.status, fidelity: fid.status, killing_checks: fails.slice(0, 12), killing_count: fails.length })
  writeFileSync(join(work, 'mutation-results.json'), JSON.stringify(results, null, 1) + '\n')
  console.log(name, status, `smoke=${smoke.status} fidelity=${fid.status} fails=${fails.length}`, fails.slice(0, 3).join(' | '))
}
writeFileSync(join(work, 'mutation-results.json'), JSON.stringify(results, null, 1) + '\n')
console.log(`mutants=${results.length} killed=${results.filter(r => r.status === 'KILLED').length} survived=${results.filter(r => r.status === 'SURVIVED').length} not_applied=${results.filter(r => r.status === 'NOT-APPLIED').length}`)
if (results.some(r => r.name.startsWith('C0 ') ? r.status !== 'SURVIVED' : r.status !== 'KILLED')) process.exit(1)
console.log('DONE')
