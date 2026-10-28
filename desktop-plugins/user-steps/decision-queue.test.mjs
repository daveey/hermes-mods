import { test, after } from 'node:test'
import assert from 'node:assert/strict'
import { createRequire, register } from 'node:module'
import { resolve } from 'node:path'
import { homedir } from 'node:os'
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || `${homedir()}/.hermes/hermes-agent/apps/desktop`, 'package.json'))
const React = require('react'), J = require('react/jsx-runtime')
const { JSDOM } = require('jsdom')
const dom = new JSDOM('<div id="root"></div>', { url: 'https://example.test' })
globalThis.window = dom.window; globalThis.document = dom.window.document
// React's input polyfill checks this at import time.
Object.defineProperty(globalThis, 'navigator', { value: dom.window.navigator, configurable: true })
globalThis.IS_REACT_ACT_ENVIRONMENT = true
const atom = value => ({ value, get() { return this.value } })
const host = { state: { focusedStoredSessionId: atom('S1'), connectionId: atom('C1'), profile: atom('default'), focusedSessionOwner: atom({ connectionId: 'C1', profile: 'default' }) }, composer: { submit() { assert.fail('no composer delivery for decisions') }, setDraft() {}, focus() {} } }
globalThis.queueHarness = { React, J, host }
register('data:text/javascript,' + encodeURIComponent(`export async function resolve(s,c,n){const map={'react':'const R=globalThis.queueHarness.React;export const useState=R.useState,useEffect=R.useEffect,useRef=R.useRef','react/jsx-runtime':'const J=globalThis.queueHarness.J;export const jsx=J.jsx,jsxs=J.jsxs','@hermes/plugin-sdk':'export const host=globalThis.queueHarness.host,COMPOSER_AREAS={top:"composer.top"},TRANSCRIPT_DIRECTIVE_AREA="transcript.directives",useValue=a=>a?.get(),MessageTextContent=({text})=>text'};return map[s]?{url:'data:text/javascript,'+encodeURIComponent(map[s]),shortCircuit:true}:n(s,c)}`))
const m = await import('./plugin.js')
const { createRoot } = require('react-dom/client')
const root = createRoot(document.getElementById('root'))
const el = () => document.getElementById('root')
const flush = () => new Promise(r => setTimeout(r, 0))
const show = async node => React.act(async () => { root.render(node); await flush() })
const click = async label => React.act(async () => { const b = [...el().querySelectorAll('button')].find(b => b.textContent === label); assert.ok(b, label); b.click(); await flush() })
const owner = { gatewayId: 'G1', profile: 'default', storedSessionId: 'S1' }
const alias = { gatewayId: 'G1', profile: 'default', type: 'kanban', board: 'ops', id: 'same' }
const request = extra => ({ schemaVersion: 2, owner, requestId: '00000000-0000-4000-8000-000000000001', revision: 1, kind: 'approval', title: 'Stage A', scope: { complete: true, markdown: 'Exact adapter files, correctness tests and deliverables.\n' + 'Full inline plan. '.repeat(300), sourceRefs: [] }, limits: [{ key: 'CPU', value: '≤4 CPU core-hours' }, { key: 'RAM', value: '≤4 GiB RAM' }, { key: 'GPU', value: '≤2 existing GPU-hours', permissionRequired: 'owner permission' }], exclusions: ['No rentals or new spend', 'Full training needs separate budget approval'], outcomes: { approve: 'Stage A only', revise: 'Work remains blocked', decline: 'No execution' }, choices: [{ id: 'yes', label: 'Approve Stage A', effect: 'approve' }, { id: 'edit', label: 'Revise', effect: 'revise' }, { id: 'no', label: 'Decline', effect: 'decline' }], aliases: [alias], state: 'open', ...extra })
const route = { connectionId: 'C1', profile: 'default', session: 'S1' }
let serial = 0
const freshRequest = extra => request({ requestId: `00000000-0000-4000-8000-${String(++serial).padStart(12, '0')}`, ...extra })
after(async () => { await React.act(async () => root.unmount()); dom.window.close() })

test('canonical projection uses exact identity and qualified aliases only', () => {
 const r = freshRequest(), other = request({ requestId: '00000000-0000-4000-8000-000000000002' })
 assert.equal(m.projectRequests([r,r,other]).length, 2)
 assert.equal(m.requestForSource([r], alias), r)
 for (const change of [{ gatewayId: 'G2' }, { profile: 'work' }, { board: 'other' }, { gatewayId: undefined }]) assert.equal(m.requestForSource([r], { ...alias, ...change }), null)
 assert.equal(m.requestForSource([r], { ...alias, id: 'similar title' }), null)
})

test('missing scope disables approve; exact limits and long scope stay inline; receipt is passive', async () => {
 const r = request({ scope: { complete: false, markdown: 'Missing exact plan', sourceRefs: [] } })
 await show(React.createElement(m.DecisionRow, { request: r, route, call: async () => ({}) }))
 assert.equal([...el().querySelectorAll('button')].find(b => b.textContent === 'Approve').disabled, true)
 for (const s of ['≤4 CPU core-hours','≤4 GiB RAM','owner permission','Full training needs separate budget approval']) assert.ok(el().textContent.includes(s))
 for (const b of el().querySelectorAll('button')) assert.equal(b.style.minHeight, '44px')
 await show(React.createElement(m.StepBox, { attrs: { id: 'us_abcdef01', s: 'S1', t: 'Original receipt ~/plan.md' }, deps: { call: async () => ({ step: { id: 'us_abcdef01', text: 'Original', status: 'open' } }) } }))
 assert.equal(el().querySelectorAll('button').length, 0)
 assert.match(el().textContent, /Original receipt/)
 assert.ok(el().querySelector('a'))
})

test('revise focuses inline, validates, Escape restores focus; keyboard submits durable revision', async () => {
 const calls = []; let op = null; const r = freshRequest()
 const call = async (path, opts = {}) => { calls.push([path,opts]); if (opts.method === 'POST') { op = { operationId: 'op-revise', state: 'pending' }; return { operation: op, request: r } } return { operation: op, request: r } }
 await show(React.createElement(m.DecisionRow, { key: 'revise', request: r, route, call }))
 await click('Request changes')
 let input = el().querySelector('textarea'); assert.equal(document.activeElement, input)
 assert.equal([...el().querySelectorAll('button')].find(b => b.textContent === 'Send revision').disabled, true)
 await React.act(async () => { input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); await flush() })
 assert.equal(el().querySelector('textarea'), null); assert.equal(document.activeElement.textContent, 'Request changes')
 await click('Request changes'); input = el().querySelector('textarea')
 await React.act(async () => { Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'Change the CPU limit'); input.dispatchEvent(new dom.window.Event('input',{bubbles:true})); await flush() })
 await React.act(async () => { input.dispatchEvent(new dom.window.KeyboardEvent('keydown',{key:'Enter',ctrlKey:true,bubbles:true})); await flush() })
 const posts = calls.filter(([,o]) => o.method === 'POST'); assert.equal(posts.length,1)
 assert.equal(posts[0][1].body.revisionText,'Change the CPU limit'); assert.equal(posts[0][1].body.choiceId,'edit')
 assert.match(el().textContent,/recorded.*pending/i); assert.doesNotMatch(el().textContent,/Confirmed/)
})

test('lost response is reconciled before retry; same key; only readback confirms delivered', async () => {
 const calls = []; const r = freshRequest(); let attempts = 0
 const call = async (path, opts={}) => { calls.push([path,opts]); if(opts.method==='POST'){ attempts++; if(attempts===1) throw Error('lost response'); return { operation:{operationId:'op1',state:'delivered'},request:r } } if(path.includes('/operations?')) return {operation:null,request:r}; return {operation:{operationId:'op1',state:'delivered'},request:{...r,state:'resolved',resolution:{choiceId:'yes',operationId:'op1',revision:1}}} }
 await show(React.createElement(m.DecisionRow,{key:'retry',request:r,route,call}))
 await click('Approve'); assert.match(el().textContent,/unknown|lost response/i)
 await click('Retry')
 const posts=calls.filter(([,o])=>o.method==='POST'); assert.equal(posts.length,2)
 assert.equal(posts[0][1].body.idempotencyKey,posts[1][1].body.idempotencyKey)
 assert.ok(calls.findIndex(([p])=>p.includes('/operations?')) < calls.findIndex(([,o])=>o===posts[1][1]))
 assert.match(el().textContent,/Confirmed/)
})

test('late decision response after profile switch cannot update another pane', async () => {
 let finish; const r=request(); const call=()=>new Promise(resolve=>{finish=resolve})
 await show(React.createElement(m.DecisionRow,{key:'race',request:r,route,call}))
 await click('Decline'); host.state.focusedSessionOwner.value={connectionId:'C1',profile:'other'}
 await React.act(async()=>{finish({operation:{operationId:'late',state:'delivered'},request:{...r,state:'resolved'}});await flush()})
 assert.doesNotMatch(el().textContent,/Confirmed/)
 host.state.focusedSessionOwner.value={connectionId:'C1',profile:'default'}
})

test('double clicks and two mounted tiles submit once; failed delivery remains locked', async () => {
 const r=freshRequest(); let finish; const calls=[]
 const call=async(path,opts={})=>{calls.push([path,opts]); if(opts.method==='POST') return new Promise(resolve=>{finish=resolve}); return {operation:{operationId:'partial',dispatchState:'delivery_failed'},request:r}}
 await show(React.createElement('div',{},[React.createElement(m.DecisionRow,{key:'tile-a',request:r,route,call}),React.createElement(m.DecisionRow,{key:'tile-b',request:r,route,call})]))
 await React.act(async()=>{const buttons=[...el().querySelectorAll('button')].filter(b=>b.textContent==='Approve'); buttons[0].click();buttons[0].click();buttons[1].click();await flush()})
 assert.equal(calls.filter(([,o])=>o.method==='POST').length,1)
 const [path,opts]=calls.find(([,o])=>o.method==='POST')
 assert.equal(path,`/api/plugins/user-steps/requests/${r.requestId}/decisions`)
 assert.equal(opts.connectionId,'C1');assert.equal(opts.profile,'default')
 assert.equal(opts.body.choiceId,'yes');assert.equal(opts.body.expectedRevision,1);assert.equal(opts.body.session,'S1')
 await React.act(async()=>{finish({operation:{operationId:'partial',dispatchState:'delivery_failed'},request:r});await flush()})
 assert.equal(el().querySelectorAll('[role="alert"]').length,2)
 assert.doesNotMatch(el().textContent,/Confirmed/)
 assert.equal([...el().querySelectorAll('button')].filter(b=>b.textContent==='Approve').length,0)
})

test('409 reads changed scope, preserves revision text, requires fresh decision', async () => {
 const r=freshRequest(); const next={...r,revision:2,scope:{...r.scope,markdown:'New exact scope'}}
 const calls=[]; const call=async(path,opts={})=>{calls.push([path,opts]);if(opts.method==='POST')throw Object.assign(Error('conflict'),{status:409});return {request:next}}
 await show(React.createElement(m.DecisionRow,{key:'conflict',request:r,route,call}))
 await click('Approve')
 assert.match(el().textContent,/New exact scope/);assert.match(el().textContent,/review required/i)
 assert.doesNotMatch(el().textContent,/Confirmed/)
 await click('Decline')
 assert.equal(calls.filter(([,o])=>o.method==='POST')[1][1].body.expectedRevision,2)
})

test('queue registration uses canonical region; aliases suppress only exact references and progress', async()=>{
 const r=freshRequest(); r.aliases.push({gatewayId:'G1',profile:'default',type:'user_step',id:'us_12345678'})
 const cards=[{id:'same',board:'ops',gatewayId:'G1',profile:'default',loaded:true,title:'Stage A',text:'Original card',human_request:{interaction:'approval',action:'Approve',why_human:'owner',scope:'stage',evidence:'record'}},{id:'same',board:'other',gatewayId:'G1',profile:'default',loaded:true,title:'Stage A',text:'Separate board',human_request:{interaction:'approval',action:'Approve',why_human:'owner',scope:'stage',evidence:'record'}}]
 window.__blockedPanel={snapshot:{stored:'S1',items:cards}}
 window.hermesDesktop={api:async({path})=>path.includes('/requests')?{requests:[r,r]}:{steps:[{id:'us_12345678',gatewayId:'G1',profile:'default',text:'Original step',status:'open'}]}}
 const registrations=[];const dispose=m.default.register({register:c=>registrations.push(c)})
 await React.act(async()=>{await m.refreshRequests();await m.refresh()})
 await show(registrations[0].render())
 assert.equal(el().querySelectorAll('[data-action-request]').length,1)
 assert.equal(el().querySelectorAll('[data-user-steps-step]').length,0)
 assert.equal(el().querySelectorAll('[data-user-steps-card]').length,1)
 assert.match(el().querySelector('[data-user-steps-card]').textContent,/Separate board/)
 assert.equal(el().querySelector('[data-action-scope]').textContent,r.scope.markdown)
 const progress=await import('../session-tasks/plugin.js')
 const chip={kind:'kanban',gatewayId:'G1',profile:'default',board:'ops',id:'same',state:'blocked'}
 assert.equal(progress.progressChips([chip]).length,0)
 assert.equal(progress.progressChips([{...chip,board:'other'},{...chip,state:'running'},{kind:'bg'}]).length,3)
 await show(null);dispose();delete window.hermesDesktop;delete window.__blockedPanel
})

test('out of order same-session refresh and owner switches do not publish stale requests',async()=>{
 const old=freshRequest({title:'Old'}),fresh=freshRequest({title:'Fresh'});let finish
 const first=m.refreshRequests(()=>new Promise(resolve=>{finish=resolve}))
 await m.refreshRequests(async()=>({requests:[fresh]}))
 finish({requests:[old]});await first
 window.hermesDesktop={api:async()=>({requests:[fresh]})}
 await show(React.createElement(m.ActionQueue,{key:'queue-race'}))
 assert.match(el().textContent,/Fresh/);assert.doesNotMatch(el().textContent,/Old/)
 host.state.focusedSessionOwner.value={connectionId:'C2',profile:'default'}
 await show(React.createElement(m.ActionQueue,{key:'queue-race'}))
 assert.doesNotMatch(el().textContent,/Old/)
 host.state.focusedSessionOwner.value={connectionId:'C1',profile:'default'}
 await show(null);delete window.hermesDesktop
})

test('cold reload recovers retry key but confirms only server readback; malformed requests fail closed',async()=>{
 const r=freshRequest();const d={session:'S1',expectedRevision:1,choiceId:'yes',idempotencyKey:'retained-client-key'}
 const key=JSON.stringify(['C1','default','S1'])+':'+m.requestKey(r)
 window.localStorage.setItem('hermes.actionIntent.v2:'+key,JSON.stringify(d))
 const calls=[];const cold=await import('./plugin.js?cold-reload')
 const call=async(path,opts={})=>{calls.push([path,opts]);return {operation:{operationId:'recover',dispatchState:'pending'},request:r}}
 await show(React.createElement(cold.DecisionRow,{request:r,route,call}))
 assert.match(el().textContent,/recorded.*pending/i)
 assert.ok(calls[0][0].includes('idempotencyKey=retained-client-key'))
 assert.equal(calls.filter(([,o])=>o.method==='POST').length,0)
 assert.equal([...el().querySelectorAll('button')].some(b=>b.textContent==='Approve'),false)
 for(const change of [{limits:null},{choices:[{id:'x',label:'Go',effect:'execute'}]},{scope:null},{requestId:'malformed'}])assert.equal(cold.projectRequests([{...r,...change}]).length,0)
 await show(null)
})

test('operation lookup 404 permits same-key resend; failed or mismatched readback never confirms',async()=>{
 const r=freshRequest();let posts=0;const bodies=[]
 const call=async(path,opts={})=>{
  if(opts.method==='POST'){posts++;bodies.push(opts.body);if(posts===1)throw Error('connection lost');return {operation:{operationId:'mismatch',dispatchState:'delivered'},request:r}}
  if(path.includes('/operations?'))throw Object.assign(Error('not found'),{status:404})
  return {operation:{operationId:'mismatch',dispatchState:'delivered'},request:{...r,state:'resolved',resolution:{operationId:'wrong',choiceId:'yes',revision:1}}}
 }
 await show(React.createElement(m.DecisionRow,{key:'404',request:r,route,call}))
 await click('Approve');await click('Retry')
 assert.equal(posts,2);assert.equal(bodies[0].idempotencyKey,bodies[1].idempotencyKey)
 assert.doesNotMatch(el().textContent,/Confirmed/);assert.match(el().textContent,/unknown/i)
})

test('confirmed readback fans out to passive qualified transcript receipt without changing original text',async()=>{
 const r=freshRequest();r.aliases.push({gatewayId:'G1',profile:'default',type:'user_step',id:'us_87654321'})
 await m.refreshRequests(async()=>({requests:[r]}))
 const step={id:'us_87654321',status:'open',text:'Original request',requestId:r.requestId,requestOwner:owner}
 const call=async(path,opts={})=>path.includes('/step?')?{step}:opts.method==='POST'?{operation:{operationId:'receipt',dispatchState:'delivered'},request:r}:{operation:{operationId:'receipt',dispatchState:'delivered'},request:{...r,state:'resolved',resolution:{operationId:'receipt',choiceId:'yes',revision:1}}}
 await show(React.createElement('div',{},[React.createElement(m.DecisionRow,{key:'row',request:r,route,call}),React.createElement(m.StepBox,{key:'receipt',attrs:{id:step.id,s:'S1',t:'Exact original transcript text'},deps:{call}})]))
 await click('Approve')
 const receipt=el().querySelector('[data-user-step]')
 assert.match(receipt.textContent,/Confirmed/)
 assert.equal(receipt.querySelector('[data-user-step-text]').textContent,'Exact original transcript text')
 assert.equal(receipt.querySelectorAll('button').length,0)
})

test('pending operation from another window retries exact server operation without a local intent',async()=>{
 const r=freshRequest({resolution:{operationId:'crash-op',choiceId:'yes',revision:1}})
 let delivered=false;const calls=[]
 const call=async(path,opts={})=>{calls.push([path,opts]);if(opts.method==='POST'){assert.match(path,/\/operations\/crash-op\/retry\?session=S1$/);assert.equal(opts.body,undefined);delivered=true}
 return {operation:{operationId:'crash-op',dispatchState:delivered?'delivered':'pending'},request:{...r,state:delivered?'resolved':'open'}}}
 await show(React.createElement(m.DecisionRow,{key:'pending-recovery',request:r,route,call}))
 assert.match(el().textContent,/delivery pending/)
 await click('Retry')
 assert.equal(calls.filter(([,o])=>o.method==='POST').length,1)
 assert.match(el().textContent,/Confirmed/)
})

test('cancelled dispatch is not success or retry; replacement revision requires fresh permission', async()=>{
 const r=freshRequest({state:'cancelled',resolution:{operationId:'cancel-op',choiceId:'yes',revision:1}})
 const calls=[]
 const call=async(path,opts={})=>{calls.push([path,opts]);return {operation:{operationId:'cancel-op',dispatchState:'cancelled'},request:r}}
 await show(React.createElement(m.DecisionRow,{key:'cancel-recovery',request:r,route,call}))
 assert.match(el().textContent,/not started.*no permission/i)
 assert.doesNotMatch(el().textContent,/Confirmed|Recorded choice: Approve Stage A\. Stage A only/)
 assert.equal([...el().querySelectorAll('button')].some(b=>b.textContent==='Retry'),false)
 assert.match(el().textContent,/replacement revision/i)
 const next={...r,revision:2,state:'open',resolution:undefined}
 await show(React.createElement(m.DecisionRow,{key:'cancel-recovery',request:next,route,call}))
 assert.ok([...el().querySelectorAll('button')].some(b=>b.textContent==='Approve'))
 assert.equal(calls.filter(([,o])=>o.method==='POST').length,0)
})

test('multiple approval alternatives retain distinct labels and exact choice IDs',async()=>{
 const r=freshRequest({kind:'choice',choices:[{id:'draft',label:'Open draft PR',effect:'approve'},{id:'ready',label:'Open ready PR',effect:'approve'},{id:'revise',label:'Revise',effect:'revise'}]})
 const calls=[]
 const call=async(path,opts={})=>{calls.push([path,opts]);return {request:r,operation:{operationId:'multi',dispatchState:'pending'}}}
 await show(React.createElement(m.DecisionRow,{key:'multiple',request:r,route,call}))
 assert.equal([...el().querySelectorAll('button')].some(b=>b.textContent==='Approve'),false)
 assert.ok([...el().querySelectorAll('button')].some(b=>b.textContent==='Open draft PR'))
 await click('Open ready PR')
 assert.equal(calls.filter(([,o])=>o.method==='POST').length,1)
 assert.equal(calls.find(([,o])=>o.method==='POST')[1].body.choiceId,'ready')
})

test('clarifications preserve labels and concrete answers submit without free text',async()=>{
 for (const kind of ['choice','input']) {
  const r=freshRequest({kind,scope:{complete:true,markdown:'Which format?',sourceRefs:[]},outcomes:{answer:'Answer only, no execution.'},choices:[{id:'json',label:'JSON document',effect:'answer'},{id:'answer-text',label:'Something else',effect:'answer'}]})
  assert.equal(m.validRequest(r),true)
  const calls=[];const call=async(path,opts={})=>{calls.push([path,opts]);return {request:r,operation:{operationId:'answer-'+kind,dispatchState:'pending'}}}
  await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call}))
  assert.equal(el().querySelector('[data-approval-blocked]'),null)
  assert.equal(el().querySelector('[data-action-scope]').getAttribute('aria-label'),'Question')
  assert.deepEqual([...el().querySelectorAll('button')].map(b=>b.textContent),['JSON document','Something else'])
  await click('JSON document')
  assert.equal(el().querySelector('textarea'),null)
  const posts=calls.filter(([,o])=>o.method==='POST');assert.equal(posts.length,1)
  assert.equal(posts[0][1].body.choiceId,'json');assert.equal(posts[0][1].body.revisionText,'JSON document')
  assert.equal(posts[0][1].body.expectedRevision,1)
 }
})

test('answer-text focuses and validates free text, then sends an answer rather than revision',async()=>{
 const r=freshRequest({kind:'input',outcomes:{answer:'Answer only.'},choices:[{id:'answer-text',label:'Type an answer',effect:'answer'}]})
 const posts=[];const call=async(path,opts={})=>{if(opts.method==='POST')posts.push(opts.body);return {request:r,operation:{operationId:'typed-answer',dispatchState:'pending'}}}
 await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call}))
 await click('Type an answer')
 const input=el().querySelector('textarea');assert.equal(document.activeElement,input)
 await click('Send answer');assert.equal(posts.length,0)
 await React.act(async()=>{Object.getOwnPropertyDescriptor(dom.window.HTMLTextAreaElement.prototype,'value').set.call(input,'Plain text please');input.dispatchEvent(new dom.window.Event('input',{bubbles:true}));await flush()})
 await click('Send answer');assert.equal(posts.length,1)
 assert.equal(posts[0].revisionText,'Plain text please');assert.equal(posts[0].choiceId,'answer-text')
})

test('incomplete approvals await agent completion while legacy clarification labels stay literal',async()=>{
 for(const kind of ['approval','choice','input']) {
  const r=freshRequest({kind,state:'needs_context',scope:{complete:false,markdown:'Which change?',sourceRefs:[]},choices:[{id:'context',label:'Explain the target',effect:'revise'}]})
  await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call:async()=>assert.fail('no submission')}))
  const labels=[...el().querySelectorAll('button')].map(b=>b.textContent)
  assert.equal(labels.includes('Request full plan inline'),false)
  if(kind==='approval'){
   assert.match(el().querySelector('[data-agent-completion-pending]').textContent,/Agent completion pending/)
   assert.deepEqual(labels,['Approve']);assert.equal(el().querySelector('button').disabled,true)
  }else{
   assert.equal(el().querySelector('[data-approval-blocked]'),null)
   assert.deepEqual(labels,['Explain the target'])
  }
 }
})

test('legacy "Request complete action" placeholder never renders for any kind (Diplomacy repro)',async()=>{
 const legacy=[{id:'request-context',label:'Request complete action',effect:'revise'}]
 for(const kind of ['approval','external_step','choice','input']) {
  const r=freshRequest({kind,state:'needs_context',title:'Decide whether web Diplomacy gets a labeled animation',scope:{complete:false,markdown:"Should it make a clearly labeled animation rebuilt from the replay's recorded moves, or should web Diplomacy be left out of the clip set?",sourceRefs:[]},outcomes:{revise:'Request complete action; keep work blocked.'},choices:legacy})
  await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call:async()=>assert.fail('no submission')}))
  assert.doesNotMatch(el().textContent,/Request complete action/,kind)
  const labels=[...el().querySelectorAll('button')].map(b=>b.textContent)
  if(kind==='approval') assert.deepEqual(labels,['Approve'])
  else assert.deepEqual(labels,[],kind)
  if(['approval','external_step'].includes(kind)) assert.match(el().querySelector('[data-agent-completion-pending]').textContent,/Agent completion pending/)
  else assert.match(el().querySelector('[data-approval-blocked]').textContent,/Answer in chat/)
 }
})

test('incomplete external steps await agent completion; complete external steps keep their choices',async()=>{
 const pending=freshRequest({kind:'external_step',state:'needs_context',scope:{complete:false,markdown:'Plug in the cable',sourceRefs:[]},choices:[{id:'ctx',label:'Explain',effect:'revise'}]})
 await show(React.createElement(m.DecisionRow,{key:pending.requestId,request:pending,route,call:async()=>assert.fail('no submission')}))
 assert.deepEqual([...el().querySelectorAll('button')].map(b=>b.textContent),[])
 const done=freshRequest({kind:'external_step',choices:[{id:'did',label:'I did it',effect:'approve'},{id:'edit',label:'Change it',effect:'revise'}]})
 await show(React.createElement(m.DecisionRow,{key:done.requestId,request:done,route,call:async()=>assert.fail('no submission')}))
 assert.deepEqual([...el().querySelectorAll('button')].map(b=>b.textContent),['I did it','Change it'])
 assert.equal(el().querySelector('[data-agent-completion-pending]'),null)
})

test('For you hides closed and superseded requests but retains operation readback until terminal confirmation',async()=>{
 const closed=freshRequest({state:'resolved',title:'Old resolved'}),superseded=freshRequest({state:'superseded',title:'Old superseded'}),cancelled=freshRequest({state:'cancelled',title:'Old cancelled'})
 const pending=freshRequest({state:'resolved',title:'Pending receipt',resolution:{operationId:'queue-readback',choiceId:'yes',revision:1}})
 let delivered=false;const reads=[]
 window.hermesDesktop={api:async({path})=>{if(path.includes('/operations/')){reads.push(path);return {request:pending,operation:{operationId:'queue-readback',dispatchState:delivered?'delivered':'pending'}}}return {requests:[closed,superseded,cancelled,pending]}}}
 await show(React.createElement(m.ActionQueue,{key:'closed-filter'}))
 assert.equal(el().querySelectorAll('[data-action-request]').length,1)
 assert.match(el().textContent,/Pending receipt/);assert.doesNotMatch(el().textContent,/Old resolved|Old superseded|Old cancelled/)
 assert.ok(reads.length>0)
 delivered=true;await click('Check status')
 assert.equal(el().querySelector('[data-action-queue]'),null)
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.equal(el().querySelector('[data-action-queue]'),null,'confirmed receipt stays out on refresh')
 await show(null);delete window.hermesDesktop
})

test('backend failure and malformed owner render inline errors and disable cached authorization',async()=>{
 const r=freshRequest()
 window.hermesDesktop={api:async()=>({requests:[r]})}
 await show(React.createElement(m.ActionQueue,{key:'outage'}))
 window.hermesDesktop.api=async()=>{throw Error('backend unavailable')}
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.match(el().textContent,/Action queue unavailable/)
 assert.equal([...el().querySelectorAll('button')].find(b=>b.textContent==='Approve').disabled,true)
 window.hermesDesktop.api=async()=>({requests:[{...r,owner:{...owner,storedSessionId:'sibling-session'}}]})
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.match(el().textContent,/Invalid action request owner/)
 await show(null);delete window.hermesDesktop
})

test('backfilled legacy rows: real Diplomacy options, Done for user steps, agent-pending approvals not counted For you',async()=>{
 const span="Should it make a clearly labeled animation rebuilt from the replay's recorded moves, or should web Diplomacy be left out of the clip set?"
 const diplo=freshRequest({kind:'choice',title:'Decide whether web Diplomacy gets a labeled animation',revision:2,scope:{complete:true,markdown:span,sourceRefs:[]},outcomes:{answer:'Send this answer to the agent. This does not grant execution permission.'},choices:[{id:'answer-0',label:"Make a clearly labeled animation rebuilt from the replay's recorded moves",effect:'answer'},{id:'answer-1',label:'Should web Diplomacy be left out of the clip set',effect:'answer'},{id:'answer-text',label:'Answer in your own words',effect:'answer'}]})
 const step=freshRequest({kind:'external_step',title:'Record your voice',revision:2,scope:{complete:true,markdown:'Record your voice, then say ready.',sourceRefs:[]},outcomes:{answer:'Tell the agent. This does not grant execution permission.'},choices:[{id:'answer-done',label:'Done',effect:'answer'},{id:'answer-text',label:"Can't do it / reply in your own words",effect:'answer'}]})
 const ap=freshRequest({kind:'approval',title:'Consent to a disconnect',revision:2,state:'needs_context',scope:{complete:false,markdown:'That disruption needs consent.',sourceRefs:[]},outcomes:{revise:'Waiting for the agent to publish the full exact plan.'},choices:[{id:'revise',label:'Request changes',effect:'revise'}]})
 window.hermesDesktop={api:async()=>({requests:[diplo,step,ap]})}
 await show(React.createElement(m.ActionQueue,{key:'backfilled'}))
 assert.doesNotMatch(el().textContent,/Request complete action/)
 assert.match(el().querySelector('h2').textContent,/^For you \(2\)$/)
 const labels=[...el().querySelectorAll('button')].map(b=>b.textContent)
 for(const want of ["Make a clearly labeled animation rebuilt from the replay's recorded moves",'Should web Diplomacy be left out of the clip set','Answer in your own words','Done',"Can't do it / reply in your own words"]) assert.ok(labels.includes(want),want)
 assert.ok(!labels.includes('Request changes'),'agent-pending approval has no revise button')
 const apRow=[...el().querySelectorAll('[data-action-request]')].find(a=>a.getAttribute('aria-label')==='Consent to a disconnect')
 assert.match(apRow.textContent,/Agent completion pending/)
 assert.doesNotMatch(apRow.textContent,/Request changes:/,'no revise outcome line while the plan is missing')
 window.hermesDesktop={api:async()=>({requests:[ap]})}
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.match(el().querySelector('h2').textContent,/^Waiting on the agent \(1\)/)
 await show(null);delete window.hermesDesktop
})
