import { test } from 'node:test'
import assert from 'node:assert/strict'
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
export async function resolve(spec, ctx, next) {
  if (spec === '@hermes/plugin-sdk') return {url:'data:text/javascript,export const COMPOSER_AREAS={};export const host={};export const Codicon=()=>null;export const DisclosureCaret=()=>null;export const useValue=()=>null',shortCircuit:true}
  if (spec === 'react' || spec === 'react/jsx-runtime') return {url:'data:text/javascript,export const useEffect=()=>{};export const useRef=v=>({current:v});export const useState=v=>[v,()=>{}];export const jsx=(type,props)=>({type,props});export const jsxs=jsx',shortCircuit:true}
  return next(spec,ctx)
}`))
const m = await import('../plugin.js')
const request = {action:'On your test Mac, allow Screen Recording. Unblock = verify; archive = stop.',why_human:'Only its owner can grant access',interaction:'physical',scope:'DEVICE: test Mac',evidence:'permission denied'}
const chip = (id='root', connectionId='C') => ({id,kind:'kanban',connectionId,profile:'default',board:'default',state:'todo'})
const row = c => ({key:c.id,kind:'kanban',label:c.id,chip:c})
const detail = (id,status,parents=[],extra={}) => ({task:{id,status,title:id,...extra},links:{parents},link_tasks:[]})
const fixture = () => ({root:detail('root','todo',['running','middle']),running:detail('running','running'),middle:detail('middle','todo',['ask']),ask:detail('ask','blocked',[],{block_kind:'needs_input',human_request:request})})
const reader = ds => (c,id) => ds[id] ? {state:'ok',info:m.cardSummary(id,{detail:ds[id],board:'default'})} : {state:'failed',info:null}
test('all dependency branches expose the actual upstream action, not only the first running branch', () => {
  const ds=fixture(), r=m.inheritedAsks([row(chip())],Date.now(),{info:reader(ds)})
  assert.equal(r.asks.length,1)
  assert.deepEqual(r.asks[0].path,['root','middle','ask'])
  assert.equal(r.asks[0].request.action,request.action)
  assert.equal(r.asks[0].chip.id,'ask')
  assert.equal(r.asks[0].chip.connectionId,'C')
  assert.equal(r.incomplete,false)
})
test('resolved or invalid asks and finished dependency branches never resurrect human attention', () => {
  for (const patch of [{status:'done'},{human_request:{action:'not validated'}},{block_kind:'time'},{status:'ready'}]) {
    const ds=fixture(); Object.assign(ds.ask.task,patch)
    assert.equal(m.inheritedAsks([row(chip())],Date.now(),{info:reader(ds)}).asks.length,0)
  }
  const ds=fixture(); ds.middle.task.status='done'
  assert.equal(m.inheritedAsks([row(chip())],Date.now(),{info:reader(ds)}).asks.length,0)
})
test('stale and unreadable evidence stays unknown; no action is inferred from titles', () => {
  const ds=fixture(), read=reader(ds)
  const info=(c,id)=>id==='ask'?{...read(c,id),state:'stale'}:read(c,id)
  const r=m.inheritedAsks([row(chip())],Date.now(),{info})
  assert.equal(r.asks.length,0); assert.equal(r.incomplete,true)
  assert.equal(m.inheritedAsks([row({...chip(),stale:true})],Date.now(),{info:read}).asks.length,0)
})
test('cycle bounded, shared asks deduped per route, separate connections preserved', () => {
  const ds=fixture(); ds.middle.links.parents.push('root'); ds.other=detail('other','todo',['ask'])
  const rows=[row(chip()),row(chip('other')),row(chip('root','D'))]
  const r=m.inheritedAsks(rows,Date.now(),{info:reader(ds)})
  assert.equal(r.asks.length,2)
  assert.deepEqual(r.asks.map(a=>a.chip.connectionId),['C','D'])
})
test('legacy summaries, graph caps and depth truncation explicitly remain incomplete', () => {
  const legacy = () => ({state:'ok',info:{status:'todo',hop:{leaf:{kind:'slot'}}}})
  assert.equal(m.inheritedAsks([row(chip())],Date.now(),{info:legacy}).incomplete,true)
  const many = {root:detail('root','todo',Array.from({length:100},(_,n)=>`p${n}`))}
  for(let n=0;n<100;n++) many[`p${n}`]=detail(`p${n}`,'ready')
  assert.equal(m.inheritedAsks([row(chip())],Date.now(),{info:reader(many)}).incomplete,true)
  const deep = (c,id) => ({state:'ok',info:{status:'todo',parents:[`${id}x`],board:'default'}})
  assert.equal(m.inheritedAsks([row(chip())],Date.now(),{info:deep}).incomplete,true)
})
test('persisted summaries cannot assert a current ask while their re-read is pending', async () => {
  const values=new Map(), ds=fixture(), c=chip()
  const old=globalThis.window
  globalThis.window={localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},hermesDesktop:{api:()=>new Promise(()=>{})}}
  try {
    m.resetReadsForTests()
    for(const id of Object.keys(ds)) m.cardInfo(c,id,Date.now(),async()=>({detail:ds[id],board:'default'}))
    await new Promise(resolve=>setImmediate(resolve))
    assert.equal(m.inheritedAsks([row(c)]).asks.length,1)
    m.writeReads(); m.resetReadsForTests()
    const r=m.inheritedAsks([row(c)])
    assert.equal(r.asks.length,0); assert.equal(r.incomplete,true)
  } finally { m.resetReadsForTests(); globalThis.window=old }
})
test('fresh responses preserve missing graph provenance through the real summary/cache path', async () => {
  const old=globalThis.window
  try {
    for (const graph of [{}, {links:{}}, {links:{parents:null}}, {links:{parents:[]}}]) {
      m.resetReadsForTests()
      globalThis.window={hermesDesktop:{api:async()=>({task:{id:'root',status:'todo'},...graph})}}
      m.inheritedAsks([row(chip())]); await new Promise(resolve=>setImmediate(resolve))
      const r=m.inheritedAsks([row(chip())])
      assert.equal(r.incomplete,!Array.isArray(graph.links?.parents))
      assert.equal(r.asks.length,0)
    }
  } finally {m.resetReadsForTests();globalThis.window=old}
})
test('same-id different-board cache reads cannot publish another board human action', async () => {
  const old=globalThis.window, calls=[]
  globalThis.window={hermesDesktop:{api:async({path})=>{
    const board=new URL(path,'http://example.com').searchParams.get('board')
    const id=path.split('/').pop().split('?')[0]; calls.push(`${board}:${id}`)
    return detail(id,id==='ask'?'blocked':'todo',id==='root'?['ask']:[],id==='ask'?{block_kind:'needs_input',human_request:{...request,action:`${board} action`}}:{})
  }}}
  try {
    m.resetReadsForTests()
    const a={...chip(),board:'A'}, b={...chip(),board:'B'}
    for(let n=0;n<3;n++){m.inheritedAsks([row(a)]);await new Promise(resolve=>setImmediate(resolve))}
    assert.equal(m.inheritedAsks([row(a)]).asks[0].request.action,'A action')
    const cold=m.inheritedAsks([row(b)])
    assert.equal(cold.asks.length,0);assert.equal(cold.incomplete,true)
    for(let n=0;n<3;n++){await new Promise(resolve=>setImmediate(resolve));m.inheritedAsks([row(b)])}
    const r=m.inheritedAsks([row(b)])
    assert.equal(r.asks[0].request.action,'B action');assert.equal(r.asks[0].chip.board,'B')
    assert.ok(calls.includes('B:root'));assert.ok(calls.includes('B:ask'))
    m.writeReads();m.resetReadsForTests()
  } finally {m.resetReadsForTests();globalThis.window=old}
})
test('feed and park seeded answers remain board-qualified and legacy persisted keys are ignored', async () => {
  const old=globalThis.window, values=new Map()
  globalThis.window={localStorage:{getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)},hermesDesktop:{api:()=>new Promise(()=>{})}}
  try {
    for (const seed of ['feed','park','legacy']) {
      m.resetReadsForTests()
      const ds=fixture(), a={...chip(),board:'A'}, b={...chip(),board:'B'}
      if(seed==='feed') m.ingestBundle('C','default',{session:'test',cards:Object.fromEntries(Object.entries(ds).map(([id,d])=>[id,{board:'A',detail:d}]))})
      if(seed==='park') {
        globalThis.window.hermesDesktop.api=async()=>ds.root
        m.parkFor(a)
        await new Promise(resolve=>setImmediate(resolve))
        globalThis.window.hermesDesktop.api=()=>new Promise(()=>{})
      }
      if(seed==='legacy') values.set('hermes.sessionPanel.reads.v1',JSON.stringify({cards:[['C|default|root',{info:m.cardSummary('root',{board:'A',detail:ds.root})}]]}))
      const r=m.inheritedAsks([row(b)])
      assert.equal(r.asks.length,0);assert.equal(r.incomplete,true)
    }
  } finally {m.resetReadsForTests();globalThis.window=old}
})
test('read-only upstream row renders exact action, dependency provenance, scope and qualified source', () => {
  const a=m.inheritedAsks([row(chip())],Date.now(),{info:reader(fixture())}).asks[0]
  const tree=m.UpstreamAsk({ask:a}), text=JSON.stringify(tree)
  assert.ok(text.includes(request.action)); assert.ok(text.includes('root → middle → ask'))
  assert.ok(text.includes('DEVICE: test Mac')); assert.ok(text.includes('C · default · default'))
  assert.ok(!text.includes('data-ask-done')); assert.ok(!text.includes('data-ask-skip'))
})
