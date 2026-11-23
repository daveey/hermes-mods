import {spawnSync} from 'node:child_process'
import assert from 'node:assert/strict'
const plan='# Plan\n\n- Inspect the existing implementation carefully.\n- Run the relevant targeted tests.'
const ask='::user-step{kind="confirm" prompt="Ready?"}'
function scan(reply){const r=spawnSync(process.execPath,['scanner.mjs'],{input:JSON.stringify({reply}),encoding:'utf8'});assert.equal(r.status,0,r.stderr);return JSON.parse(r.stdout)}
let count=0
for(const [name,reply,ok] of [
 ['list',plan+'\n\n'+ask,true],
 ['url',plan+'\n\n::user-step{url="https://example.com/a_b?x=1&y=2"}',true],
 ['inline',plan+'\n\nPlease '+ask,false],
 ['nested',plan+'\n\n> '+ask,false],
 ['multiple',plan+'\n\n'+ask+'\n\n'+ask,false],
 ['unknown',plan+'\n\n::other{}\n\n'+ask,false],
 ['malformed',plan+'\n\n::user-step{kind="x" garbage}',false],
 ['duplicate attr',plan+'\n\n::user-step{kind="x" kind="y"}',false],
 ['short list','- A\n- B\n\n'+ask,false],
 ['trailing',plan+'\n\n'+ask+'\n\nLater',false],
 ['code',plan+'\n\n```js\n'+ask+'\n```',false],
 ['ordinary confirmation',plan+'\n\nShould I proceed?',false],
 ['prose','# Plan\n\n'+Array(26).fill('implementation').join(' ')+'\n\n'+ask,true],
 ['no heading',Array(26).fill('implementation').join(' ')+'\n\n'+ask,false],
 ['prefix frozen','Introduction must survive.\n\n'+plan+'\n\n'+ask,true],
 ['reasoning hidden duplicate','<think>::other{}</think>\n'+plan+'\n\n'+ask,false]
]) {const result=scan(reply);assert.equal(result!==null,ok,name);if(ok){assert.equal(result.ask.source,reply.slice(reply.lastIndexOf('::')));assert(result.plan.includes('Plan'))} count++}
assert.equal(scan('Introduction must survive.\n\n'+plan+'\n\n'+ask).plan,'Introduction must survive.\n\n'+plan)
console.log(`${count} scanner smoke cases passed; prefix preservation verified`)
