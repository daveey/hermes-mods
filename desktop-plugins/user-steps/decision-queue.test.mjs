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
 assert.equal([...el().querySelectorAll('button')].some(b => b.textContent === 'Approve'), false, 'no greyed-out Approve for a plan-less ask')
 assert.doesNotMatch(el().textContent, /keep work blocked|Work stays blocked/)
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
   assert.match(el().querySelector('[data-agent-completion-pending]').textContent,/asked you to approve this without a plan/)
   assert.deepEqual(labels,['Ask agent for the plan','Dismiss'])
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
  if(kind==='approval') assert.deepEqual(labels,['Ask agent for the plan','Dismiss'])
  else if(kind==='external_step') assert.deepEqual(labels,['Ask agent for the full step','Dismiss'])
  else assert.deepEqual(labels,[],kind)
  if(['approval','external_step'].includes(kind)) assert.match(el().querySelector('[data-agent-completion-pending]').textContent,/without a (plan|complete step)/)
  else assert.match(el().querySelector('[data-approval-blocked]').textContent,/Answer in chat/)
 }
})

test('incomplete external steps await agent completion; complete external steps keep their choices',async()=>{
 const pending=freshRequest({kind:'external_step',state:'needs_context',scope:{complete:false,markdown:'Plug in the cable',sourceRefs:[]},choices:[{id:'ctx',label:'Explain',effect:'revise'}]})
 await show(React.createElement(m.DecisionRow,{key:pending.requestId,request:pending,route,call:async()=>assert.fail('no submission')}))
 assert.deepEqual([...el().querySelectorAll('button')].map(b=>b.textContent),['Ask agent for the full step','Dismiss'])
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
 assert.match(apRow.textContent,/asked you to approve this without a plan/)
 assert.doesNotMatch(apRow.textContent,/Request changes:/,'no revise outcome line while the plan is missing')
 window.hermesDesktop={api:async()=>({requests:[ap]})}
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.match(el().querySelector('h2').textContent,/^Waiting on the agent \(1\)/)
 await show(null);delete window.hermesDesktop
})

test('panel minimizes to one header line, stays minimized across remount, and reopens for a new request',async()=>{
 window.localStorage.clear()
 const a=freshRequest({title:'Put the doc on the hub'})
 let list=[a]
 window.hermesDesktop={api:async()=>({requests:list})}
 await show(React.createElement(m.ActionQueue,{key:'min-1'}))
 const toggle=()=>el().querySelector('[data-action-queue-toggle]')
 assert.equal(toggle().getAttribute('aria-expanded'),'true')
 assert.ok(el().querySelector('[data-action-request]'))
 await React.act(async()=>{toggle().click();await flush()})
 assert.equal(toggle().getAttribute('aria-expanded'),'false')
 assert.equal(el().querySelector('[data-action-request]'),null,'body hidden')
 assert.match(el().querySelector('h2').textContent,/^For you \(1\)$/)
 assert.match(el().querySelector('[data-action-queue-summary]').textContent,/Put the doc on the hub/)
 await show(null);await show(React.createElement(m.ActionQueue,{key:'min-2'}))
 assert.equal(el().querySelector('[data-action-request]'),null,'minimized survives remount')
 list=[a,freshRequest({title:'Brand new ask'})]
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.equal(el().querySelectorAll('[data-action-request]').length,2,'new request reopens the panel')
 await React.act(async()=>{toggle().click();await flush()})
 await React.act(async()=>{toggle().click();await flush()})
 assert.equal(el().querySelectorAll('[data-action-request]').length,2,'expand restores body')
 await show(null);delete window.hermesDesktop;window.localStorage.clear()
})

test('busy session: approve collapses the row, auto-retries the same operation until delivered, then confirms',async()=>{
 m.autoRetry && (m.autoRetry.ms=20)
 const r=freshRequest(); const calls=[]; let delivered=false
 const busyOp={operationId:'busy-op',dispatchState:'failed',error:'Session is busy with another turn; this decision is delivered automatically when it ends.'}
 const resolved={...r,state:'resolved',resolution:{choiceId:'yes',operationId:'busy-op',revision:1}}
 const call=async(path,opts={})=>{calls.push([path,opts])
  if(path.includes('/retry')){delivered=true;return {}}
  if(opts.method==='POST') return {operation:busyOp,request:{...r,resolution:{choiceId:'yes',operationId:'busy-op',revision:1}}}
  return delivered?{operation:{...busyOp,dispatchState:'delivered',error:undefined},request:resolved}:{operation:busyOp,request:{...r,resolution:{choiceId:'yes',operationId:'busy-op',revision:1}}}}
 await show(React.createElement(m.DecisionRow,{key:'busy',request:r,route,call}))
 assert.ok(el().querySelector('[data-action-scope]'),'plan shown before deciding')
 await click('Approve')
 assert.match(el().textContent,/session is busy/i)
 assert.equal(el().querySelector('[data-action-scope]'),null,'plan collapses once decided')
 assert.doesNotMatch(el().textContent,/≤4 GiB RAM/)
 await React.act(async()=>{await new Promise(res=>setTimeout(res,80))})
 const retries=calls.filter(([p])=>p.includes('/retry'))
 assert.equal(retries.length,1,'one automatic retry of the same operation')
 assert.match(retries[0][0],/operations\/busy-op\/retry/)
 assert.equal(calls.filter(([p,o])=>o.method==='POST'&&p.includes('/decisions')).length,1,'no second decision')
 assert.match(el().textContent,/Confirmed/)
 m.autoRetry && (m.autoRetry.ms=15000)
})

test('plan-less approval: Ask agent for the plan drafts the ask, Dismiss hides it until a new revision (desktop-1321064734)',async()=>{
 const drafts=[];const prev=host.composer.setDraft;host.composer.setDraft=(sid,text)=>drafts.push([sid,text])
 const r=freshRequest({title:'Open a pull request implementing these strategy changes',state:'needs_context',scope:{complete:false,markdown:'Do you want me to open a PR?',sourceRefs:[]},outcomes:{revise:'Waiting for the agent'},choices:[{id:'revise',label:'Request changes',effect:'revise'}]})
 window.hermesDesktop={api:async()=>({requests:[r]})}
 await show(React.createElement(m.ActionQueue,{key:'planless'}))
 assert.equal(el().querySelectorAll('[data-action-request]').length,1)
 assert.doesNotMatch(el().textContent,/Request changes|keep work blocked|Work stays blocked/)
 await click('Ask agent for the plan')
 assert.equal(drafts.length,1);assert.equal(drafts[0][0],'S1')
 assert.match(drafts[0][1],/full plan/i);assert.match(drafts[0][1],/Open a pull request implementing these strategy changes/)
 await click('Dismiss')
 assert.equal(el().querySelectorAll('[data-action-request]').length,0)
 window.hermesDesktop.api=async()=>({requests:[{...r,revision:2}]})
 await React.act(async()=>{await m.refreshRequests();await flush()})
 assert.equal(el().querySelectorAll('[data-action-request]').length,1,'a new revision shows again')
 host.composer.setDraft=prev;window.localStorage.clear();await show(null);delete window.hermesDesktop
})

test('reported PR question renders its real option as a button plus a free-text answer (desktop-1321064734)',async()=>{
 const r=freshRequest({kind:'choice',title:'Choose: open a PR or something else',scope:{complete:true,markdown:'Do you mean open a pull request implementing these strategy changes, or something else?',sourceRefs:[]},outcomes:{answer:'Send this answer to the agent. This does not grant execution permission.'},choices:[{id:'answer-0',label:'Open a pull request implementing these strategy changes',effect:'answer'},{id:'answer-text',label:'Answer in your own words',effect:'answer'}]})
 await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call:async()=>({})}))
 const labels=[...el().querySelectorAll('button')].map(b=>b.textContent)
 assert.equal(labels.length,2);assert.ok(labels[0].includes('Open a pull request'))
 assert.equal(el().querySelector('[data-agent-completion-pending]'),null)
 assert.doesNotMatch(el().textContent,/full exact plan|Work stays blocked/)
})

test('run-timing question marks its go options as approving (self-heal agent-aa8d66631c)',async()=>{
 const r=freshRequest({kind:'choice',title:'When should I run the migration?',scope:{complete:true,markdown:'When should I run the Mac mini launcher migration: now, when idle, or later?',sourceRefs:[]},outcomes:{answer:'Send this answer to the agent. Options marked ✓ approve running exactly the scope above; any other answer (and free text) grants no execution permission.'},choices:[{id:'answer-0',label:'now',effect:'answer',authorizes:true},{id:'answer-1',label:'later',effect:'answer'},{id:'answer-text',label:'Answer in your own words',effect:'answer'}]})
 assert.equal(m.validRequest(r),true)
 await show(React.createElement(m.DecisionRow,{key:r.requestId,request:r,route,call:async()=>({})}))
 const labels=[...el().querySelectorAll('button[data-decision-effect]')].map(b=>b.textContent)
 assert.deepEqual(labels,['✓ now','later','Answer in your own words'])
 // the go option says it approves; the others carry no such line (no per-option disclaimer either)
 assert.equal(el().querySelector('[data-decision-option="answer-0"]').textContent,'✓ nowApproves running exactly the plan above.')
 assert.equal(el().querySelector('[data-decision-option="answer-1"]'),null)
 assert.doesNotMatch(el().textContent,/grants no execution permission/)
})

// ── Withdrawn asks: retired without a decision; nothing is sent to the agent ──
const withdrawnBy = (by, reason) => ({ by, reason, at: '2026-10-27T11:00:00+00:00' })
const pingWithdraw = { ok: true, api: 2, features: ['withdraw'] }
const offered = () => [...el().querySelectorAll('button')].some(b => b.textContent === 'Not needed')
// Withdrawable asks carry a user_step alias; a kanban alias means a bound decision card, which decides it instead.
const askable = extra => freshRequest({ aliases: [{ gatewayId: 'G1', profile: 'default', type: 'user_step', id: `us_${++serial}` }], ...extra })
const QUEUE_READ = '/api/plugins/user-steps/requests?session=S1&include=withdrawn'

test('validRequest accepts withdrawn requests carrying createdAt and withdrawal; unknown states still fail closed', () => {
 const gone = freshRequest({ state: 'withdrawn', createdAt: '2026-10-27T10:00:00+00:00', withdrawal: withdrawnBy('system', 'Card t_1418b643 finished') })
 assert.equal(m.validRequest(gone), true)
 assert.equal(m.projectRequests([gone]).length, 1)
 assert.equal(m.validRequest(freshRequest({ createdAt: '2026-10-27T10:00:00+00:00' })), true)
 assert.equal(m.validRequest(freshRequest({ state: 'withdrawn' })), true, 'a withdrawn row without its record still grants nothing')
 assert.equal(m.validRequest(freshRequest({ state: 'dismissed' })), false)
})

test('withdrawn requests are read explicitly but never rendered or counted; all withdrawn renders nothing', async () => {
 const live = freshRequest({ title: 'Still needed' }), context = freshRequest({ title: 'Needs context', state: 'needs_context' })
 const gone = freshRequest({ title: 'Moot ask', state: 'withdrawn', withdrawal: withdrawnBy('system', 'Card t_1418b643 finished') })
 const paths = []; let list = [live, gone, context]
 window.hermesDesktop = { api: async ({ path }) => { paths.push(path); return path.includes('/requests?') ? { requests: list } : {} } }
 await show(React.createElement(m.ActionQueue, { key: 'withdrawn-hidden' }))
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.ok(paths.includes(QUEUE_READ), paths.join('\n'))
 assert.ok(paths.filter(p => p.includes('/requests?')).every(p => p === QUEUE_READ), 'every queue read asks for withdrawn rows')
 assert.deepEqual([...el().querySelectorAll('[data-action-request]')].map(a => a.getAttribute('aria-label')), ['Still needed', 'Needs context'])
 // v10: an approval still waiting for its plan is agent work, shown but not counted For you.
 assert.equal(el().querySelector('[data-action-queue] h2').textContent, 'For you (1)')
 assert.doesNotMatch(el().textContent, /Moot ask|Action queue unavailable/)
 list = [gone]
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.equal(el().querySelector('[data-action-queue]'), null)
 assert.equal(el().innerHTML, '')
 await show(null); delete window.hermesDesktop
})

test('a withdrawn ask still retires its own aliases but never shadows a live request for the same source', () => {
 const gone = freshRequest({ state: 'withdrawn', withdrawal: withdrawnBy('user', 'Dismissed in Desktop as not needed') }), live = freshRequest()
 assert.equal(m.requestForSource([gone], alias), gone)
 assert.equal(m.requestForSource([gone, live], alias), live)
 assert.equal(m.requestForSource([live, freshRequest()], alias), null, 'two live claims stay ambiguous')
})

test('transcript receipts of a withdrawn ask read No longer needed, never decide above the composer', async () => {
 m.clearStepCache()
 const gone = freshRequest({ state: 'withdrawn', withdrawal: withdrawnBy('system', 'Card t_1418b643 finished') })
 const bare = freshRequest({ state: 'withdrawn', withdrawal: withdrawnBy('agent', '') })
 const settled = freshRequest({ state: 'withdrawn', withdrawal: withdrawnBy('agent', 'Answered in chat') })
 const steps = {}
 for (const [r, id, status] of [[gone, 'us_0d0d0d01', 'open'], [bare, 'us_0d0d0d02', 'open'], [settled, 'us_0d0d0d03', 'resolved']]) {
  r.aliases.push({ gatewayId: 'G1', profile: 'default', type: 'user_step', id })
  steps[id] = { id, status, text: `ask ${id}`, requestId: r.requestId, requestOwner: owner, ...(status === 'resolved' ? { resolution: 'withdrawn: Answered in chat' } : {}) }
 }
 await m.refreshRequests(async () => ({ requests: [gone, bare, settled] }))
 const call = async path => ({ step: steps[new URL(path, 'http://x').searchParams.get('id')] || null })
 await show(React.createElement('div', {}, Object.keys(steps).map(id => React.createElement(m.StepBox, { key: id, attrs: { id, s: 'S1', t: `Original ${id}` }, deps: { call } }))))
 const box = id => el().querySelector(`[data-user-step="${id}"]`)
 assert.equal(box('us_0d0d0d01').querySelector('small').textContent, 'No longer needed \u2014 Card t_1418b643 finished')
 assert.equal(box('us_0d0d0d02').querySelector('small').textContent, 'No longer needed.')
 assert.equal(box('us_0d0d0d03').querySelector('[data-user-step-resolved]').textContent, 'No longer needed \u2014 Answered in chat')
 assert.equal(box('us_0d0d0d01').querySelector('[data-user-step-text]').textContent, 'Original us_0d0d0d01')
 assert.doesNotMatch(el().textContent, /decide above the composer|\u2713/)
 assert.equal(el().querySelectorAll('button').length, 0)
})

test('a multi-step directive marks only the withdrawn step as no longer needed', async () => {
 m.clearStepCache()
 const gone = freshRequest({ state: 'withdrawn', withdrawal: withdrawnBy('agent', 'Answered in chat') }), live = freshRequest()
 gone.aliases.push({ gatewayId: 'G1', profile: 'default', type: 'user_step', id: 'us_0e0e0e01' })
 live.aliases.push({ gatewayId: 'G1', profile: 'default', type: 'user_step', id: 'us_0e0e0e02' })
 await m.refreshRequests(async () => ({ requests: [gone, live] }))
 const steps = { us_0e0e0e01: { id: 'us_0e0e0e01', status: 'open', text: 'First ask', requestOwner: owner }, us_0e0e0e02: { id: 'us_0e0e0e02', status: 'open', text: 'Second ask', requestOwner: owner } }
 const call = async path => ({ step: steps[new URL(path, 'http://x').searchParams.get('id')] || null })
 await show(React.createElement(m.StepBox, { key: 'two-steps', attrs: { id: 'us_0e0e0e01,us_0e0e0e02', s: 'S1', t: 'Two asks' }, deps: { call } }))
 assert.deepEqual([...el().querySelectorAll('[data-user-step] small')].map(s => s.textContent), ['No longer needed \u2014 Answered in chat', 'Reference only \u2014 review and decide above the composer.'])
})

test('a withdrawn ask does not resurface as a legacy reference or a blocked progress chip', async () => {
 const gone = freshRequest({ title: 'Dismissed ask', state: 'withdrawn', withdrawal: withdrawnBy('user', 'Dismissed in Desktop as not needed') })
 const card = { id: 'same', board: 'ops', gatewayId: 'G1', profile: 'default', loaded: true, title: 'Stage A', text: 'Original card', human_request: { interaction: 'approval', action: 'Approve', why_human: 'owner', scope: 'stage', evidence: 'record' } }
 window.__blockedPanel = { snapshot: { stored: 'S1', items: [card] } }
 window.hermesDesktop = { api: async ({ path }) => path.includes('/requests?') ? { requests: [gone] } : path.includes('/steps?') ? { steps: [] } : {} }
 const registrations = []; const dispose = m.default.register({ register: c => registrations.push(c) })
 await React.act(async () => { await m.refreshRequests(); await m.refresh() })
 await show(registrations[0].render())
 assert.equal(el().querySelector('[data-action-request]'), null)
 assert.equal(el().querySelector('[data-user-steps-card]'), null, 'its aliased card stays suppressed')
 assert.equal(el().textContent, '')
 const progress = await import('../session-tasks/plugin.js')
 assert.equal(progress.progressChips([{ kind: 'kanban', gatewayId: 'G1', profile: 'default', board: 'ops', id: 'same', state: 'blocked' }]).length, 0)
 await show(null); dispose(); delete window.hermesDesktop; delete window.__blockedPanel
})

test('Not needed withdraws once through the owner route even on double click, then refreshes', async () => {
 m.clearFeatureCache?.()
 const r = askable({ title: 'Stale ask' }), keep = askable({ title: 'Live ask' })
 const calls = []; let finish, list = [r, keep]
 window.hermesDesktop = { api: async ({ path, ...opts }) => {
  calls.push([path, opts])
  if (path === '/api/plugins/user-steps/ping') return pingWithdraw
  if (opts.method === 'POST') return new Promise(resolve => { finish = resolve })
  return path.startsWith('/api/plugins/user-steps/requests?') ? { requests: list } : {}
 } }
 await show(React.createElement(m.ActionQueue, { key: 'withdraw-once' }))
 await React.act(async () => { await m.refreshRequests(); await flush() })
 const tile = [...el().querySelectorAll('article')].find(a => a.getAttribute('aria-label') === 'Stale ask')
 const button = [...tile.querySelectorAll('button')].find(b => b.textContent === 'Not needed')
 assert.ok(button, 'an open ask offers Not needed')
 assert.deepEqual([...button.parentElement.children].map(b => b.textContent), ['Approve', 'Request changes', 'Decline', 'Not needed'], 'after the decision buttons')
 assert.equal(button.style.minHeight, '44px'); assert.equal(button.style.whiteSpace, 'normal')
 assert.equal(button.title, 'Remove this ask without deciding; nothing is sent to the agent')
 button.focus()
 await React.act(async () => { button.click(); button.click(); await flush() })
 const posts = () => calls.filter(([, o]) => o.method === 'POST')
 assert.equal(posts().length, 1, 'double click withdraws once')
 const [path, opts] = posts()[0]
 assert.equal(path, `/api/plugins/user-steps/requests/${r.requestId}/withdraw`)
 assert.equal(opts.connectionId, 'C1'); assert.equal(opts.profile, 'default')
 assert.deepEqual(opts.body, { session: 'S1', expectedRevision: 1, reason: 'Dismissed in Desktop as not needed' })
 assert.equal(button.disabled, true)
 assert.ok([...tile.querySelectorAll('[data-decision-effect]')].every(b => b.disabled), 'no decision while withdrawing')
 await click('Approve')
 assert.equal(posts().length, 1)
 const sent = calls.length
 const gone = { ...r, state: 'withdrawn', withdrawal: withdrawnBy('user', 'Dismissed in Desktop as not needed') }
 list = [gone, keep]
 await React.act(async () => { finish({ request: gone }); await flush(); await flush() })
 assert.ok(calls.slice(sent).some(([p, o]) => p === QUEUE_READ && !o.method), 'refreshes the queue after withdrawing')
 assert.deepEqual([...el().querySelectorAll('article')].map(a => a.getAttribute('aria-label')), ['Live ask'])
 assert.equal(el().querySelector('[data-action-queue] h2').textContent, 'For you (1)')
 assert.equal(document.activeElement.getAttribute('aria-label'), 'Live ask', 'keyboard focus stays in the queue')
 assert.equal(posts().length, 1)
 assert.equal(calls.filter(([p]) => p.endsWith('/ping')).length, 1, 'one ping for the scope')
 await show(null); delete window.hermesDesktop
})

test('Not needed is not offered while a decision is submitting, pending or recorded', async () => {
 m.clearFeatureCache?.()
 const r = askable(); const calls = []; let finish
 const pending = { operation: { operationId: 'op-lock', state: 'pending' }, request: r }
 const call = async (path, opts = {}) => { calls.push([path, opts]); if (path.endsWith('/ping')) return pingWithdraw; if (opts.method === 'POST') return new Promise(resolve => { finish = resolve }); return pending }
 await show(React.createElement(m.DecisionRow, { key: 'withdraw-lock', request: r, route, call }))
 assert.equal(offered(), true, 'offered while undecided')
 await click('Approve')
 assert.equal(offered(), false, 'hidden while the decision is submitting')
 await React.act(async () => { finish(pending); await flush() })
 assert.match(el().textContent, /delivery pending/)
 assert.equal(offered(), false, 'hidden while delivery is pending')
 const resolution = { operationId: 'op-recorded', choiceId: 'yes', revision: 1 }
 for (const recorded of [askable({ resolution }), askable({ state: 'resolved', resolution }), askable({ state: 'superseded' }), askable({ state: 'cancelled' })]) {
  const readback = async path => path.endsWith('/ping') ? pingWithdraw : { operation: { operationId: 'op-recorded', state: 'pending' }, request: recorded }
  await show(React.createElement(m.DecisionRow, { key: `recorded-${recorded.requestId}`, request: recorded, route, call: readback }))
  assert.equal(offered(), false, `${recorded.state}${recorded.resolution ? ' with a recorded decision' : ''}`)
 }
 assert.equal(calls.filter(([p]) => p.endsWith('/withdraw')).length, 0)
})

test('Not needed appears only when the owner ping advertises withdraw; one ping per scope', async () => {
 for (const [label, ping, expected] of [
  ['older backend', async () => ({ ok: true, api: 2 }), false],
  ['ping error', async () => { throw Error('ping failed') }, false],
  ['other features', async () => ({ ok: true, api: 2, features: ['other'] }), false],
  ['withdraw feature', async () => pingWithdraw, true]
 ]) {
  m.clearFeatureCache?.()
  const pings = []
  const call = async (path, opts = {}) => { if (path === '/api/plugins/user-steps/ping') { pings.push(opts); return ping() } return {} }
  await show(React.createElement('div', { key: label }, [React.createElement(m.DecisionRow, { key: 'a', request: askable(), route, call }), React.createElement(m.DecisionRow, { key: 'b', request: askable(), route, call })]))
  assert.equal(offered(), expected, label)
  assert.equal(pings.length, 1, `${label}: one ping for two tiles`)
  assert.equal(pings[0].connectionId, 'C1'); assert.equal(pings[0].profile, 'default')
  await show(React.createElement(m.DecisionRow, { key: `${label}-again`, request: askable(), route, call }))
  assert.equal(offered(), expected, `${label} after remount`)
  assert.equal(pings.length, label === 'ping error' ? 2 : 1, `${label}: an answer is cached for the scope, a failure is not`)
 }
})

test('Not needed: a 409 refreshes with an inline note; other failures keep the tile with an inline error', async () => {
 m.clearFeatureCache?.()
 const r = askable({ title: 'Contested ask' }); const calls = []; let failure = Object.assign(Error('conflict'), { status: 409 })
 const call = async (path, opts = {}) => {
  calls.push([path, opts])
  if (path.endsWith('/ping')) return pingWithdraw
  if (opts.method === 'POST') throw failure
  return path.includes('/requests?') ? { requests: [r] } : {}
 }
 await show(React.createElement(m.DecisionRow, { key: 'withdraw-409', request: r, route, call }))
 await click('Not needed')
 assert.ok(calls.some(([p, o]) => p === QUEUE_READ && !o.method), 'refreshed after the 409')
 assert.equal(el().querySelector('[data-withdraw-note]')?.textContent, 'This ask changed or was already decided; refreshed.')
 assert.equal(el().querySelector('[role="alert"]'), null)
 failure = Error('backend unavailable')
 await click('Not needed')
 assert.match(el().querySelector('[role="alert"]')?.textContent || '', /backend unavailable/)
 assert.equal(el().querySelector('[data-withdraw-note]'), null)
 assert.ok(el().querySelector('[data-action-request]'), 'tile kept')
 assert.equal([...el().querySelectorAll('button')].find(b => b.textContent === 'Not needed')?.disabled, false, 'can try again')
 assert.equal(calls.filter(([, o]) => o.method === 'POST').length, 2)
})

test('askedAgo reads just now, minutes, hours and days; the tile shows it above the title only when createdAt is known', async () => {
 const now = Date.parse('2026-10-27T12:00:00Z'), ago = ms => new Date(now - ms).toISOString()
 const cases = [[0, 'asked just now'], [59_999, 'asked just now'], [60_000, 'asked 1 min ago'], [59 * 60_000 + 59_000, 'asked 59 min ago'], [3_600_000, 'asked 1 h ago'], [23 * 3_600_000 + 3_599_000, 'asked 23 h ago'], [86_400_000, 'asked 1 d ago'], [9 * 86_400_000 + 5_000, 'asked 9 d ago'], [-120_000, 'asked just now']]
 for (const [ms, text] of cases) assert.equal(m.askedAgo?.(ago(ms), now), text, `${ms} ms`)
 assert.equal(m.askedAgo?.('2026-10-27T10:59:59.123456+00:00', now), 'asked 1 h ago', 'backend isoformat parses')
 for (const missing of [undefined, null, '', 'not a date', now]) assert.equal(m.askedAgo?.(missing, now), '', String(missing))
 const aged = freshRequest({ title: 'Aged ask', createdAt: new Date(Date.now() - 5 * 60_000 - 5_000).toISOString() })
 await show(React.createElement(m.DecisionRow, { key: 'aged', request: aged, route, call: async () => ({}) }))
 const age = el().querySelector('[data-asked-age]')
 assert.equal(age?.textContent, 'asked 5 min ago')
 assert.equal(age.closest('[data-action-meta]')?.nextElementSibling, el().querySelector('h3'), 'on the line right above the title')
 assert.equal(age.getAttribute('datetime'), aged.createdAt)
 assert.equal(el().querySelector('h3').textContent, 'Aged ask')
 await show(React.createElement(m.DecisionRow, { key: 'unaged', request: freshRequest({ title: 'Legacy row' }), route, call: async () => ({}) }))
 assert.equal(el().querySelector('[data-asked-age]'), null)
})

test('a cached owner answer expires, so an owner that restarts onto a newer plugin gets Not needed', async () => {
 m.clearFeatureCache?.()
 let answer = { ok: true, api: 2 }; const pings = []
 const call = async (path, opts = {}) => { if (path.endsWith('/ping')) { pings.push(opts); return answer } return {} }
 await show(React.createElement(m.DecisionRow, { key: 'ttl-1', request: askable(), route, call }))
 assert.equal(offered(), false)
 answer = pingWithdraw
 const realNow = Date.now
 try {
  Date.now = () => realNow() + 6 * 60_000
  await show(React.createElement(m.DecisionRow, { key: 'ttl-2', request: askable(), route, call }))
  assert.equal(pings.length, 2, 'the expired answer is asked again')
  assert.equal(offered(), true)
 } finally { Date.now = realNow }
 await show(null)
})

const intents = () => Array.from({ length: window.localStorage.length }, (_, i) => window.localStorage.key(i)).filter(k => k.startsWith('hermes.actionIntent.v2:'))

test('a withdrawn ask with a stranded retry intent never renders Check status or Retry; the dead intent is dropped', async () => {
 m.clearFeatureCache?.(); window.localStorage.clear()
 const r = freshRequest({ title: 'Will be withdrawn' })
 let list = [r]; const posts = []
 window.hermesDesktop = { api: async ({ path, ...o }) => {
  if (path.endsWith('/ping')) return pingWithdraw
  if (o.method === 'POST') { posts.push(path); throw Error('network down') }
  if (path.includes('/operations?')) throw Object.assign(Error('nf'), { status: 404 })
  if (path.includes('/requests?')) return { requests: list }
  if (path.includes(`/requests/${r.requestId}?`)) return { request: list[0] }
  return {}
 } }
 await show(React.createElement(m.ActionQueue, { key: 'stranded' }))
 await React.act(async () => { await m.refreshRequests(); await flush() })
 await React.act(async () => { [...el().querySelectorAll('button')].find(b => b.textContent === 'Approve').click(); await flush(); await flush() })
 assert.equal(intents().length, 1, 'the lost POST left a retry intent')
 list = [{ ...r, state: 'withdrawn', withdrawal: withdrawnBy('system', 'Card finished') }]
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.equal(el().querySelector('article'), null, 'no tile, so no Check status or Retry')
 assert.deepEqual(intents(), [], 'the dead intent is dropped')
 await show(null); await show(React.createElement(m.ActionQueue, { key: 'stranded-2' }))
 await React.act(async () => { await m.refreshRequests(); await flush() })
 assert.equal(el().querySelector('article'), null, 'still gone after a remount')
 assert.equal(posts.length, 1, 'no further decision POST')
 await show(null); delete window.hermesDesktop; window.localStorage.clear()
})

test('a plan-less ask offers Ask agent for the plan plus Not needed when the owner can withdraw, else the local Dismiss', async () => {
 const planless = askable({ title: 'Approve without a plan', state: 'needs_context', scope: { complete: false, markdown: 'Do you mean **open a pull request**, or something else?', sourceRefs: [] }, outcomes: { revise: 'Request complete action; keep work blocked.' }, exclusions: [], choices: [{ id: 'request-context', label: 'Request complete action', effect: 'revise' }] })
 for (const [ping, want] of [[pingWithdraw, ['Ask agent for the plan', 'Not needed']], [{ ok: true, api: 2 }, ['Ask agent for the plan', 'Dismiss']]]) {
  m.clearFeatureCache?.()
  const call = async path => path.endsWith('/ping') ? ping : {}
  await show(React.createElement(m.DecisionRow, { key: want.join('+'), request: planless, route, call }))
  assert.deepEqual([...el().querySelectorAll('button')].map(b => b.textContent), want)
 }
 await show(null)
})

test('Not needed is never offered for a request bound to a decision card', async () => {
 m.clearFeatureCache?.()
 const base = freshRequest({ title: 'Card-bound ask' })
 const bound = { ...base, aliases: [{ gatewayId: base.owner.gatewayId, profile: base.owner.profile, type: 'kanban', board: 'default', id: 't_f6f6f6f6' }] }
 const call = async path => path.endsWith('/ping') ? pingWithdraw : {}
 await show(React.createElement(m.DecisionRow, { key: 'bound', request: bound, route, call }))
 assert.ok([...el().querySelectorAll('button')].some(b => b.textContent === 'Approve'), 'still decidable')
 assert.equal(offered(), false)
 await show(null)
})

test('withdrawing the last tile hands keyboard focus back to the composer', async () => {
 m.clearFeatureCache?.()
 const r = askable({ title: 'Only ask' }); let list = [r]; const focused = []
 const prev = host.composer.focus; host.composer.focus = sid => focused.push(sid)
 window.hermesDesktop = { api: async ({ path, ...o }) => {
  if (path.endsWith('/ping')) return pingWithdraw
  if (o.method === 'POST' && path.endsWith('/withdraw')) { list = [{ ...r, state: 'withdrawn', withdrawal: withdrawnBy('user', 'Dismissed in Desktop as not needed') }]; return { request: list[0] } }
  if (path.includes('/requests?')) return { requests: list }
  return {}
 } }
 try {
  await show(React.createElement(m.ActionQueue, { key: 'last-tile' }))
  await React.act(async () => { await m.refreshRequests(); await flush() })
  const button = [...el().querySelectorAll('button')].find(b => b.textContent === 'Not needed')
  button.focus()
  await React.act(async () => { button.click(); await flush(); await flush() })
  assert.equal(el().querySelector('article'), null)
  assert.deepEqual(focused, ['S1'])
 } finally { host.composer.focus = prev; await show(null); delete window.hermesDesktop }
})

test('explained options: one line each, the recommended one first and primary, plan folded behind Full plan', async () => {
 const r = freshRequest({ title: 'Switch gen-3 and gen-4 to on-demand?', choices: [
  { id: 'no', label: 'Keep spot', effect: 'decline', explain: 'No cost change; slots stay reclaimable.' },
  { id: 'edit', label: 'Revise', effect: 'revise' },
  { id: 'yes', label: 'Approve', effect: 'approve', explain: '+$500/month; 6 runner slots back today.', recommended: true }] })
 assert.equal(m.validRequest(r), true)
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}) }))
 const options = [...el().querySelectorAll('[data-decision-option]')]
 assert.deepEqual(options.map(o => o.getAttribute('data-decision-option')), ['yes', 'no'], 'recommended first; free-text revise has no line')
 assert.equal(options[0].textContent, 'Approverecommended+$500/month; 6 runner slots back today.')
 const buttons = [...el().querySelectorAll('button[data-decision-effect]')]
 assert.deepEqual(buttons.map(b => b.textContent), ['Approve', 'Keep spot', 'Request changes'])
 assert.deepEqual(buttons.map(b => b.hasAttribute('data-primary')), [true, false, false], 'exactly one primary button')
 const fold = el().querySelector('[data-action-fold]')
 assert.equal(fold.querySelector('summary').textContent, 'Full plan and limits')
 assert.ok(fold.contains(el().querySelector('[data-action-scope]')) && fold.contains(el().querySelector('[data-action-limits]')))
 assert.equal(fold.open, false, 'folded until opened')
 assert.doesNotMatch(el().textContent, /Revision 1|Read this|Your decision/)
 assert.equal(el().querySelector('[data-action-kind]').textContent, 'Approval')
})

test('a legacy question without explanations keeps its question inline and adds no option lines', async () => {
 const r = freshRequest({ kind: 'choice', title: 'Switch or keep spot?', limits: [], exclusions: [], scope: { complete: true, markdown: 'Switch gen-3 or keep spot?', sourceRefs: [] }, outcomes: { answer: 'Send this answer to the agent. This does not grant execution permission.' }, choices: [{ id: 'answer-0', label: 'Switch gen-3', effect: 'answer' }, { id: 'answer-1', label: 'Keep spot', effect: 'answer' }, { id: 'answer-text', label: 'Answer in your own words', effect: 'answer' }] })
 await show(React.createElement(m.DecisionRow, { key: r.requestId, request: r, route, call: async () => ({}) }))
 assert.equal(el().querySelector('[data-action-fold]'), null)
 assert.equal(el().querySelector('[data-action-scope]').textContent, 'Switch gen-3 or keep spot?')
 assert.equal(el().querySelectorAll('[data-decision-option]').length, 0)
 assert.doesNotMatch(el().textContent, /does not grant execution permission/)
 assert.equal(el().querySelector('[data-action-kind]').textContent, 'Question')
})

test('validRequest mirrors the server: explain is non-empty text, recommended is true on at most one choice', () => {
 const base = request().choices
 const withChoices = choices => m.validRequest(request({ choices }))
 assert.equal(withChoices(base.map(c => ({ ...c, explain: 'x' }))), true)
 assert.equal(withChoices([{ ...base[0], explain: '' }, ...base.slice(1)]), false)
 assert.equal(withChoices([{ ...base[0], recommended: false }, ...base.slice(1)]), false)
 assert.equal(withChoices([{ ...base[0], recommended: true }, { ...base[1], recommended: true }, base[2]]), false)
})

test('with the session panel loaded, the For you strip yields to it and its parts are exported for embedding', async () => {
 const areas = []
 const dispose = m.default.register({ register: a => areas.push(a) })
 try {
  const strip = areas.find(a => a.id === 'strip')
  assert.notEqual(strip.render(), null, 'draws itself without the panel')
  window.__sessionPanel = { active: true }
  assert.equal(strip.render(), null, 'the panel draws the asks instead')
  assert.equal(window.__userSteps.ActionQueue, m.ActionQueue)
  assert.equal(window.__userSteps.Strip, m.Strip)
  assert.equal(typeof window.__userSteps.openAskCount, 'function')
 } finally { delete window.__sessionPanel; dispose() }
})

test('embedded queue drops its own frame, heading and minimize control', async () => {
 const r = freshRequest({ title: 'Embedded ask' })
 const calls = []
 const call = async (path, opts) => { calls.push(path); if (path.includes('/ping')) return { ok: true, api: 2, features: [] }; return { requests: [r] } }
 await m.refreshRequests(call)
 await show(React.createElement(m.ActionQueue, { key: 'embedded', embedded: true }))
 const section = el().querySelector('[data-action-queue]')
 assert.ok(section.hasAttribute('data-embedded'))
 assert.equal(section.querySelector('[data-action-queue-header]'), null)
 assert.equal(section.style.border, '')
 assert.equal(el().querySelector('h3').textContent, 'Embedded ask')
 assert.equal(m.openAskCount(), 1)
})
