// Fidelity, entry-point and dependency-identity tests for the shipped bundle.
// Every scan runs the actual scanner.mjs entry point in a fresh Node process.
// SCANNER=<path> tests another bundle (used for mutation testing).
import {execFile, spawnSync} from 'node:child_process'
import {readFileSync} from 'node:fs'
import assert from 'node:assert/strict'
import {dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {desktopGraphCheck} from './lockgraph.mjs'
import fuzz from './fixtures/oracle/fuzz.mjs'

const here=dirname(fileURLToPath(import.meta.url))
const scanner=process.env.SCANNER ?? join(here,'scanner.mjs')
const read=f=>JSON.parse(readFileSync(join(here,f),'utf8'))
let passed=0; const failed=[]
const ok=(name,fn)=>{ try { fn() } catch(e) { failed.push(name); console.error(`FAIL ${name}: ${e.message.split('\n')[0]}`); return } passed++ }
function scanAsync(input) {
 return new Promise((resolve,reject)=>{
  const child=execFile(process.execPath,[scanner],{encoding:'utf8',timeout:20000,maxBuffer:1<<26},(err,stdout,stderr)=>{
   if(err) return reject(err)
   if(stderr) return reject(Error(`stderr: ${stderr}`))
   resolve(stdout)
  })
  child.stdin.end(input)
 })
}
async function scanAll(replies) {
 const out=new Array(replies.length); let next=0
 await Promise.all(Array.from({length:8},async()=>{ while(next<replies.length) { const i=next++; out[i]=JSON.parse(await scanAsync(JSON.stringify({reply:replies[i]}))) } }))
 return out
}

// 1. Differential corpus against the actual Desktop renderer.
const fixture=read('fixtures/desktop-oracle.json')
const cases=[...fixture.synthetic]
fixture.real_replies.forEach((reply,i)=>{ for(const [v,suffix] of Object.entries(fixture.real_variants)) {
 const id=`real/${String(i).padStart(3,'0')}/${v}`
 cases.push({id,reply:v==='plain'?reply:reply.trimEnd()+suffix,...fixture.real[id]})
}})
for(const c of fuzz) cases.push({...c,...fixture.fuzz[c.id]})
assert.equal(cases.length,3783)
const results=await scanAll(cases.map(c=>c.reply))
const verdict=new Map()
cases.forEach((c,i)=>{
 const got=results[i], source=got===null?null:got.ask.source
 verdict.set(c.id,source)
 ok(`no over-accept ${c.id}`,()=>{ if(source!==null) assert.deepEqual([c.desktop.cards,c.desktop.card_last],[[source],true]) })
 ok(`recorded verdict ${c.id}`,()=>assert.equal(source,c.scanner))
})
// Named finding cases. Over-accepted at b82df45a (Desktop shows no final card):
for(const id of ['math/unclosed-display-line-before-ask','incomplete/attr-link','incomplete/attr-link-url','incomplete/attr-image','incomplete/odd-dollars-window-bold'])
 ok(`finding F1 refuses ${id}`,()=>assert.equal(verdict.get(id),null))
// Fuzz cases over-accepted at b82df45a. Unclosed $$ (remark-math):
for(const id of ['fuzz/0996','fuzz/2856'])
 ok(`math fuzz refused ${id}`,()=>assert.equal(verdict.get(id),null))
// The ask is shown as literal code/text (tail repair or block context):
for(const id of ['fuzz/1088','fuzz/2148','fuzz/2430'])
 ok(`literal-ask fuzz refused ${id}`,()=>assert.equal(verdict.get(id),null))
// Raw HTML: an unclosed element wraps the card into a blocked link (rehype-raw);
// still over-accepted after the first F1 correction, so a raw-HTML guard exists.
for(const id of ['fuzz/0627','fuzz/0780','fuzz/1401','fuzz/2486'])
 ok(`raw html wrap refused ${id}`,()=>assert.equal(verdict.get(id),null))
// Block splitting: marked and micromark disagree about these fences, so
// Desktop renders the ask inside a code block. The baseline accepted all 15;
// only the last-block check refuses them.
for(const c of cases.filter(c=>c.id.startsWith('blocks3/')))
 ok(`fence disagreement refused ${c.id}`,()=>{ assert.deepEqual(c.desktop.cards,[]); assert.equal(verdict.get(c.id),null) })
ok('fence disagreement corpus size',()=>assert.equal(cases.filter(c=>c.id.startsWith('blocks3/')).length,15))
// Math stage: Desktop renders a display block, not a lazy list/prose continuation.
for(const id of ['math/display-after-list-lazy','math/display-after-prose'])
 ok(`math plan shape ${id}`,()=>assert.equal(verdict.get(id),null))
// Healthy ordinary controls stay accepted (math, URLs, CR/CRLF, entity cue, blocks).
for(const id of ['control/list','control/prose','control/prefix','control/url','control/www','control/email','control/angle-url','control/ordered','control/crlf','control/nested-list-plan','control/plan-code-fence','blocks/cr-only','blocks/nbsp','blocks/loose-list','blocks/setext-plan','blocks/long-plan','hidden/entity-plus-think','math/dollar-attr','math/dollar-pair-attr','math/dollar-url-attr','math/inline-in-plan','math/currency-in-plan','math/display-in-plan','math/display-in-item','math/math-fence','math/item-display'])
 ok(`control accepted ${id}`,()=>assert.notEqual(verdict.get(id),null))
// A later image-reference definition hides HTML/entity cues in the whole
// document's image alt, but Desktop's first block is parsed independently.
// This kills skipping ONLY the first block, not just deleting the whole loop.
for(const i of [0,1,4])
 ok(`first-block reference doubt ${i}`,()=>assert.equal(verdict.get(`survivors/reference/${i}`),null))
for(const id of ['blank/0','blank/1','blank/2','blank/3','blank/4','reference/2','reference/3'])
 ok(`block boundary control ${id}`,()=>assert.notEqual(verdict.get(`survivors/${id}`),null))
console.log(`${cases.length} differential cases: ${results.filter(r=>r!==null).length} accepted, ${failed.filter(f=>f.startsWith('no over-accept')).length} over-accepted`)

// 2. Entry point: malformed / too-large / ordinary stdin.
const raw=input=>{ const r=spawnSync(process.execPath,[scanner],{input,encoding:'utf8',maxBuffer:1<<26}); assert.equal(r.status,0,r.stderr); assert.equal(r.stderr,''); return r.stdout }
const LIST='# Plan\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.'
const ASK='::user-step{t="Ready?"}'
for(const [name,input] of [['empty',''],['not json','{reply:'],['array','[]'],['no reply','{}'],['number','{"reply":5}'],['null','{"reply":null}'],['binary',Buffer.from([0xff,0xfe,0x00,0x7b])],
 ['too large',JSON.stringify({reply:LIST+'\n\n'+'x '.repeat(100000)+'\n\n'+ASK})],['sentinel',JSON.stringify({reply:LIST+'\n\n::user-step{t="a\uE001"}'})]])
 ok(`entry ${name}`,()=>assert.equal(raw(input),'null\n'))
ok('entry ordinary',()=>assert.equal(JSON.parse(raw(JSON.stringify({reply:LIST+'\n\n'+ASK}))).ask.source,ASK))
ok('entry large allowed',()=>assert.equal(JSON.parse(raw(JSON.stringify({reply:'# Plan\n\n'+'- Inspect the existing implementation carefully.\n'.repeat(1200)+'\n'+ASK}))).ask.source,ASK))

// 3. Dependency identity recorded in the manifest equals the committed lock.
const manifest=read('manifest.json'), lock=read('package-lock.json')
const plain=key=>{ const k=key.split('(')[0]; return k.slice(k.lastIndexOf('@')+1) }
function replay(npmLock) {
 const errors=[]
 for(const dep of manifest.dependencies ?? []) {
  const entry=npmLock.packages[dep.path]
  if(!entry || entry.version!==dep.version || entry.integrity!==dep.integrity) errors.push(dep.path)
  for(const key of dep.desktop) if(plain(key)!==dep.version) errors.push(`${dep.path} ${key}`)
 }
 return errors
}
ok('manifest dependencies replay against package-lock.json',()=>{ assert((manifest.dependencies ?? []).length>0); assert.deepEqual(replay(lock),[]) })
// F2's six named packages, at Desktop's pnpm-lock.yaml@a960c29c versions.
const BASELINE={ // b82df45a resolutions (scanner side of the F2 table)
 'mdast-util-gfm-autolink-literal':['2.1.2','2.0.1'],'mdast-util-find-and-replace':['3.0.5','3.0.3'],'mdast-util-gfm-table':['2.0.1','2.0.0'],
 'mdast-util-gfm-task-list-item':['2.0.1','2.0.0'],'unist-util-visit':['5.2.0','5.1.0'],'unist-util-visit-parents':['6.1.0','6.0.2']}
for(const [name,[old,desktop]] of Object.entries(BASELINE)) {
 const deps=(manifest.dependencies ?? []).filter(d=>d.name===name)
 const installed=Object.entries(lock.packages).filter(([p])=>p===`node_modules/${name}` || p.endsWith(`/node_modules/${name}`))
 ok(`F2 ${name} pinned`,()=>{ assert(deps.length>0); for(const d of deps) assert.equal(d.version,desktop); assert(installed.length>0); for(const [,e] of installed) assert.equal(e.version,desktop) })
 ok(`F2 ${name} drift is RED`,()=>{
  const drifted=structuredClone(lock)
  for(const d of deps) drifted.packages[d.path]={...drifted.packages[d.path],version:old}
  assert(replay(drifted).length>0)
 })
}
ok('manifest hashes renderer wiring inputs',()=>{
 const paths=manifest.modules.map(m=>m.path)
 for(const p of ['apps/desktop/src/lib/katex-memo.ts','apps/desktop/src/lib/lru-cache.ts','apps/desktop/src/lib/markdown-blocks.ts','apps/desktop/src/components/assistant-ui/markdown-text.tsx','apps/desktop/package.json','pnpm-lock.yaml']) assert(paths.includes(p),p)
 assert((manifest.pipeline??[]).some(s=>/remark-math \{singleDollarTextMath: true\}/.test(s)))
 assert((manifest.pipeline??[]).some(s=>/tailBoundedRemend/.test(s)) && (manifest.pipeline??[]).some(s=>/parseMarkdownIntoBlocksCached/.test(s)))
})
ok('licenses keyed by full node_modules path',()=>{
 const notices=readFileSync(join(here,'THIRD-PARTY-LICENSES.txt'),'utf8')
 for(const d of manifest.dependencies ?? []) assert(notices.includes(`===== ${d.path} (${d.name}@${d.version},`),d.path)
 assert((manifest.dependencies ?? []).length>0)
})

// 4. lockgraph.mjs on synthetic graphs: each guard must refuse its fault.
function graph({version='1.0.0',integrity='sha512-A',desktopValue='1.0.0',edge=true,external=false,shipped=true,extra=false,childVersion='2.0.0',childLock='2.0.0',alias=false,npmName=alias?'real-b':undefined,noDesktopIntegrity=false,noNpmChild=false,directB=false}={}) {
 const inputs={'desktop:apps/desktop/src/lib/x.ts':{imports:[{path:'node_modules/a/index.js'},...(directB?[{path:'node_modules/b/index.js'}]:[]),...(external?[{path:'fs',external:true}]:[])]},
  'node_modules/a/index.js':{imports:[{path:'node_modules/b/index.js'}]},'node_modules/b/index.js':{imports:[]}}
 if(extra) inputs['node_modules/c/index.js']={imports:[]}
 const out={'node_modules/a/index.js':{bytesInOutput:shipped?5:0},'node_modules/b/index.js':{bytesInOutput:5},...(extra?{'node_modules/c/index.js':{bytesInOutput:5}}:{})}
 const npmLock={packages:{'node_modules/a':{version,integrity},'node_modules/b':{version:childVersion,integrity:'sha512-B',...(npmName?{name:npmName}:{})},'node_modules/c':{version:'1.0.0',integrity:'sha512-C'}}}
 if(noNpmChild) delete npmLock.packages['node_modules/b']
 const bName=alias?'real-b':'b'
 const desktopLock={importers:{'apps/desktop':{dependencies:edge?{a:{version:desktopValue}}:{}}},
  snapshots:{[`a@${desktopValue}`]:{dependencies:{b:alias?`real-b@${childLock}`:childLock}}},
  packages:{[`a@${desktopValue.split('(')[0]}`]:{resolution:{integrity:'sha512-A'}},[`${bName}@${childLock}`]:{resolution:{integrity:'sha512-B'}}}}
 if(noDesktopIntegrity) delete desktopLock.packages[`${bName}@${childLock}`]
 return desktopGraphCheck({metafile:{inputs,outputs:{'scanner.mjs':{inputs:out}}},npmLock,desktopLock})
}
ok('lockgraph GREEN',()=>{ const g=graph(); assert.deepEqual(g.errors,[]); assert.deepEqual(g.packages.map(p=>[p.path,p.version]),[['node_modules/a','1.0.0'],['node_modules/b','2.0.0']]) })
ok('lockgraph peer-suffixed value',()=>assert.deepEqual(graph({desktopValue:'1.0.0(react@19.3.0)'}).errors,[]))
ok('lockgraph alias',()=>assert.deepEqual(graph({alias:true}).errors,[]))
for(const [name,opts,want] of [['version',{version:'1.0.1'},/version 1\.0\.1 but Desktop locks 1\.0\.0/],['integrity',{integrity:'sha512-X'},/integrity sha512-X but Desktop locks sha512-A/],['missing edge',{edge:false},/edge absent from Desktop lock/],['external',{external:true},/external import fs/],['unreached',{extra:true},/node_modules\/c: bundled but not reached/],['child version',{childVersion:'2.0.1'},/version 2\.0\.1 but Desktop locks 2\.0\.0/],['child lock',{childLock:'2.0.1'},/version 2\.0\.0 but Desktop locks 2\.0\.1/],['alias target',{alias:true,npmName:'other-b'},/installs other-b but Desktop locks real-b/],['unaliased install of alias',{alias:true,npmName:null},/installs b but Desktop locks real-b/],['aliased install',{npmName:'real-b'},/installs real-b but Desktop locks b/],
 ['Desktop integrity missing',{noDesktopIntegrity:true},/integrity sha512-B but Desktop locks undefined/],['npm entry missing',{noNpmChild:true},/node_modules\/b: not in package-lock\.json/],['direct edge absent',{directB:true},/Desktop source -> b: edge absent/]])
 ok(`lockgraph RED ${name}`,()=>{ const errors=graph(opts).errors; assert(errors.some(e=>want.test(e)),errors.join('\n')) })
ok('lockgraph tree-shaken parent is not shipped',()=>{ const g=graph({shipped:false}); assert(g.errors.length>0); assert.deepEqual(g.tree_shaken,['node_modules/a']) })
ok('lockgraph no importer',()=>assert(desktopGraphCheck({metafile:{inputs:{},outputs:{}},npmLock:{packages:{}},desktopLock:{importers:{}}}).errors.length>0))
if(failed.length) { console.error(`${failed.length} fidelity checks FAILED, ${passed} passed`); process.exit(1) }
console.log(`${passed} fidelity checks passed`)
