// Run the immutable baseline and candidate entrypoints in fresh Node processes.
// node admission-floors.mjs <baseline-scanner.mjs> <output.json>
import {execFile} from 'node:child_process'
import {readFileSync,writeFileSync} from 'node:fs'
import {dirname,join,resolve} from 'node:path'
import {fileURLToPath} from 'node:url'
import assert from 'node:assert/strict'
import {createHash} from 'node:crypto'
import fuzz from './fixtures/oracle/fuzz.mjs'
const here=dirname(fileURLToPath(import.meta.url)),[baseline,output]=process.argv.slice(2)
assert(baseline&&output,'Supply immutable baseline bundle and evidence output path')
// b82df45aa52053154f71c25c5c6a7ded447d15e6: authentic pre-correction parser tree.
assert.equal(createHash('sha256').update(readFileSync(baseline)).digest('hex'),'1b79aaf38cea10535094c20a319d8e65cc7e5c8e38dbeddf15ac470869871ecd','baseline identity')
const fixture=JSON.parse(readFileSync(join(here,'fixtures/desktop-oracle.json'),'utf8'))
const cases=[...fixture.synthetic]
fixture.real_replies.forEach((reply,i)=>{for(const [v,suffix] of Object.entries(fixture.real_variants)) {
 const id=`real/${String(i).padStart(3,'0')}/${v}`
 cases.push({id,class:'real',reply:v==='plain'?reply:reply.trimEnd()+suffix,...fixture.real[id]})
}})
for(const c of fuzz) cases.push({...c,class:'fuzz',...fixture.fuzz[c.id]})
const run=(scanner,reply)=>new Promise((resolve,reject)=>{
 const child=execFile(process.execPath,[scanner],{encoding:'utf8',timeout:20000,maxBuffer:1<<26},(err,stdout,stderr)=>{
  if(err||stderr) return reject(err??Error(stderr)); try {resolve(JSON.parse(stdout))} catch(e){reject(e)}
 }); child.stdin.end(JSON.stringify({reply}))
})
const rows=new Array(cases.length); let next=0
await Promise.all(Array.from({length:8},async()=>{while(next<cases.length){
 const i=next++,c=cases[i],before=await run(resolve(baseline),c.reply),after=await run(join(here,'scanner.mjs'),c.reply)
 rows[i]={id:c.id,class:c.class??c.id.split('/')[0],before:before?.ask.source??null,after:after?.ask.source??null,desktop:c.desktop,
  ...(Boolean(before)!==Boolean(after)?{reply:c.reply}:{}),same_output:JSON.stringify(before)===JSON.stringify(after)}
}}))
const changed=rows.filter(r=>r.before!==r.after),newAllows=changed.filter(r=>r.before===null),newRefusals=changed.filter(r=>r.after===null)
const realChanges=rows.filter(r=>r.class==='real'&&!r.same_output)
const whitespaceControls=new Set(['fuzz/1438','fuzz/1885','fuzz/2022','fuzz/2228','fuzz/2384','fuzz/2569'])
const healthyChanges=rows.filter(r=>(r.id.startsWith('control/')||whitespaceControls.has(r.id))&&!r.same_output)
const summary={cases:rows.length,baselineAccepted:rows.filter(r=>r.before!==null).length,candidateAccepted:rows.filter(r=>r.after!==null).length,
 newAllows:newAllows.length,newRefusals:newRefusals.length,realChanges:realChanges.length,healthyChanges:healthyChanges.length,
 classes:Object.fromEntries([...new Set(newRefusals.map(r=>r.class))].map(k=>[k,newRefusals.filter(r=>r.class===k).length]))}
writeFileSync(output,JSON.stringify({summary,changed,realChanges,healthyChanges,rows},null,1)+'\n')
console.log(JSON.stringify(summary,null,2))
assert.equal(newAllows.length,0,'new admission versus immutable baseline')
assert.equal(realChanges.length,0,'sanitized real reply output changed')
assert.equal(healthyChanges.length,0,'ordinary control output changed')
assert.equal(rows.filter(r=>r.after!==null&&JSON.stringify([r.desktop.cards,r.desktop.card_last])!==JSON.stringify([[r.after],true])).length,0,'candidate over-accept')
