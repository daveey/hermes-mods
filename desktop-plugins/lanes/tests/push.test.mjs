import test from 'node:test'
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { loadPlugin, mount, React, host } from './sdk-mock.mjs'
const P = await loadPlugin()
const graph = { total: 83, sources: [{key:'manual',count:83}], lanes:[{key:'work',count:83}],
  sinks:[{key:'dam:you',count:83,dam:true,label:'needs you',color:'#f00',hint:'server hint'}],
  edges:{sourceLane:[{from:'manual',to:'work',count:83}],laneSink:[{from:'work',to:'dam:you',count:83}]} }
const fixture = { configured:true, totals:{running:71,open:93}, lanes:{work:{share:10,target:.7,actual:.2,drift:'server drift',
  running:7,not_ready:42,blocked_on_you:9,open:58,ready:3}},
  floor:{columns:[{key:'waiting',label:'Waiting'}],names:['work'],cells:[{lane:'work',step:'waiting',count:66,piling:true,summary:'66 server reasons',cards:[]}]},
  flow:{cards:[],lanes:{work:{}},graphs:{project:graph,source:graph}} }

test.afterEach(()=>{ localStorage.clear(); host.routes=[] })
test('pill, footer, Floor and Flow render server counts even with empty card lists', async()=>{
  localStorage.setItem(P.OPEN_KEY,'1')
  const view = await mount(React.createElement(P.Lanes,{ctx:{rest:async()=>fixture}}))
  try {
    const pill = view.container.querySelector('[data-lane-pill=work]')
    assert.equal(pill.getAttribute('aria-label'),'work: 9 blocked on you, 7 running, 42 waiting')
    assert.deepEqual([...pill.querySelectorAll('[data-count]')].map(n=>[n.dataset.count,n.textContent]),[['blocked','9'],['running','7'],['other','42']])
    assert.equal(pill.querySelector('.hermes-lanes-dot'),null)
    assert.match(view.container.textContent,/71 running · 93 open/)
    assert.equal(view.container.querySelector('[data-step=waiting]').dataset.count,'66')
    assert.equal(view.container.querySelector('[data-step=waiting]').dataset.piling,'1')
    await view.click([...view.container.querySelectorAll('button')].find(b=>b.textContent==='Flow'))
    assert.match(view.container.textContent,/83 open cards/)
    assert.equal(view.container.querySelector('[data-flow-node=source]').dataset.count,'83')
    assert.match(view.container.textContent,/server hint/)
  } finally { await view.close() }
})

test('strip: no colour square, zero lanes hidden, all-lanes status counts on the right', async()=>{
  const snap={...fixture,lanes:{work:{running:2,not_ready:0,blocked_on_you:0,open:2},
    hub:{running:9,not_ready:9,blocked_on_you:1,open:19,strip:{blocked:5,running:9,other:5}},idle:{running:0,not_ready:0,blocked_on_you:0,open:0}},
    status:{you:14,ext:43,worker:23,run:21,dep:37,triage:19,other:0}}
  const view = await mount(React.createElement(P.Lanes,{ctx:{rest:async()=>snap}}))
  try {
    const strip=view.container.querySelector('.hermes-lanes-mini')
    assert.deepEqual([...strip.querySelectorAll('[data-lane-pill]')].map(p=>p.dataset.lanePill),['work','hub'])
    assert.deepEqual([...strip.querySelector('[data-lane-pill=hub]').querySelectorAll('[data-count]')].map(n=>[n.dataset.count,n.textContent]),
      [['blocked','5'],['running','9'],['other','5']], 'hub strip counts win over the snapshot fields')
    assert.deepEqual([...strip.querySelector('[data-lane-pill=work]').querySelectorAll('[data-count]')].map(n=>n.dataset.count),['running'])
    const items=[...strip.querySelectorAll('[data-status]')]
    assert.deepEqual(items.map(i=>[i.dataset.status,i.querySelector('.hermes-lanes-n').textContent]),
      [['you','14'],['ext','43'],['worker','23'],['run','21'],['dep','37'],['triage','19']])
    assert.equal(items[0].title,'Red: blocked on you, 14 cards. Click to select')
    // The number sits inside the category square: no separate swatch beside it.
    const sq=(k)=>items.find(i=>i.dataset.status===k).querySelector('[data-card-colour]')
    assert.deepEqual(['you','ext','worker','run','dep','triage'].map(k=>[sq(k).dataset.cardColour,sq(k).textContent]),
      [['red','14'],['pink','43'],['green','23'],['blue','21'],['grey','37'],['outline','19']])
    assert.equal(strip.querySelector('.hermes-lanes-sw'),null)
    assert.equal(sq('you').style.borderColor,'rgb(239, 68, 68)'); assert.equal(sq('ext').style.borderColor,'rgb(236, 72, 153)')
  } finally { await view.close() }
})

test('strip without a hub status projection shows no totals', async()=>{
  const view = await mount(React.createElement(P.Lanes,{ctx:{rest:async()=>fixture}}))
  try { assert.equal(view.container.querySelector('[data-slot=lanes-status]'),null) } finally { await view.close() }
})

test('live subscription has no polling timer; unavailable socket alone polls at 60 seconds', async()=>{
  for(const available of [true,false]) {
    const timers=[];const cleared=[];let callback
    const source=P.createSource({rest:async()=>fixture,...(available?{socket:(_path,fn)=>{callback=fn;return ()=>{}}}:{})},host)
    const stop=source.subscribe({hours:6,window:'6h',expanded:false},()=>{},()=>{}, {
      setInterval:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearInterval:id=>cleared.push(id),
      setTimeout:()=>1,clearTimeout:()=>{}
    })
    await new Promise(r=>setTimeout(r,10))
    if(available) { callback({version:1,view:fixture}); assert.equal(timers.length,0) }
    else { assert.deepEqual(timers.map(t=>t.ms),[60000]);assert.equal(timers.length,1) }
    stop()
  }
})

test('connection-pinned native stream changes parameters without reconnecting and refreshes once on reconnect', async()=>{
  const oldBridge=window.hermesDesktop, oldWS=globalThis.WebSocket
  const sockets=[], grants=[], reads=[], intervals=new Map(), timeouts=new Map(); let serial=0
  const timers={ setInterval:(fn,ms)=>{const id=++serial;intervals.set(id,{fn,ms});return id},clearInterval:id=>intervals.delete(id),
    setTimeout:(fn,ms)=>{const id=++serial;timeouts.set(id,{fn,ms});return id},clearTimeout:id=>timeouts.delete(id) }
  window.hermesDesktop={getConnectionFor:async opts=>{assert.equal(opts.connectionId,'hub');return {authMode:'native'}},
    getPluginWsUrl:async opts=>{grants.push(opts);return 'ws://example.com/ticket'}}
  globalThis.WebSocket=class {
    constructor(url){this.url=url;this.readyState=0;this.sent=[];sockets.push(this)}
    send(text){this.sent.push(JSON.parse(text))}
    close(){this.readyState=3;this.onclose?.()}
  }
  const source=P.createSource({rest:async(path,opts)=>{reads.push([path,opts]);return fixture}}, {profileRoutes:async()=>[{connectionId:'hub',targetProfile:'default'}]})
  const stop=source.subscribe({hours:6,window:'6h',expanded:false},()=>{},()=>{},timers)
  const flush=()=>new Promise(r=>setTimeout(r,5))
  try{
    await flush();assert.equal(sockets.length,1);assert.equal(grants[0].connectionId,'hub')
    const ws=sockets[0];ws.readyState=1;ws.onopen();ws.onmessage({data:JSON.stringify({version:1,view:fixture})})
    assert.equal(intervals.size,0);assert.equal(reads.length,1)
    stop.update({hours:24,window:'24h',expanded:true})
    assert.deepEqual(ws.sent.at(-1),{hours:24,window:'24h',expanded:true})
    assert.equal(sockets.length,1);assert.equal(reads.length,1)
    const oldDeadlines=[...timeouts.keys()]
    ws.close();assert.deepEqual([...intervals.values()].map(t=>t.ms),[60000])
    assert.ok(oldDeadlines.every(id=>!timeouts.has(id)), 'disconnect clears its watchdog')
    const reconnect=[...timeouts.values()].find(t=>t.ms<30000);reconnect.fn();await flush()
    const next=sockets[1];next.readyState=1;next.onopen();next.onmessage({data:JSON.stringify({version:1,view:fixture})});await flush()
    assert.equal(intervals.size,0);assert.equal(reads.length,2)
  }finally{stop();window.hermesDesktop=oldBridge;globalThis.WebSocket=oldWS}
  assert.equal(intervals.size,0)
})

test('parameter change during initial GET opens only one stream and closes every socket', async()=>{
  const oldBridge=window.hermesDesktop, oldWS=globalThis.WebSocket
  const sockets=[], pending=[]
  window.hermesDesktop={getConnectionFor:async()=>({authMode:'native'}),getPluginWsUrl:async()=> 'ws://example.com/ticket'}
  globalThis.WebSocket=class { constructor(){this.readyState=0;sockets.push(this)} close(){this.readyState=3;this.onclose?.()} }
  const source=P.createSource({rest:()=>new Promise(resolve=>pending.push(resolve))},{profileRoutes:async()=>[{connectionId:'hub',targetProfile:'default'}]})
  const stop=source.subscribe({hours:6,window:'6h',expanded:false},()=>{},()=>{})
  const flush=()=>new Promise(r=>setTimeout(r,5))
  try {
    await flush()
    stop.update({hours:24,window:'24h',expanded:true})
    await flush()
    for(const resolve of pending) resolve(fixture)
    await flush()
    assert.equal(sockets.length,1)
  } finally {stop();window.hermesDesktop=oldBridge;globalThis.WebSocket=oldWS}
  assert.ok(sockets.every(ws=>ws.readyState===3))
})

test('no Desktop card counting outside the geometry helper',async()=>{
  let src=await readFile(new URL('../plugin.js',import.meta.url),'utf8')
  src=src.replace(/export function sankey[\s\S]*?const HOUR_WINDOW/,'const HOUR_WINDOW')
  assert.doesNotMatch(src,/cards\.length|cards\?\.length|cards\.reduce\(|for \(const c of cards\)|list\.reduce\(/)
  assert.doesNotMatch(src,/refetchInterval:/)
})

test('fallback poll reads nothing while the window is hidden', async () => {
  const timers=[]; let reads=0
  const source=P.createSource({rest:async()=>{reads++;return fixture}},host)
  const stop=source.subscribe({hours:6,window:'6h',expanded:false},()=>{},()=>{}, {
    setInterval:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearInterval:()=>{},
    setTimeout:()=>1,clearTimeout:()=>{}
  })
  await new Promise(r=>setTimeout(r,10))
  assert.deepEqual(timers.map(t=>t.ms),[60000])
  const before=reads
  Object.defineProperty(document,'hidden',{value:true,configurable:true})
  try { timers[0].fn(); await new Promise(r=>setTimeout(r,5)); assert.equal(reads,before,'hidden: no read') }
  finally { delete document.hidden }
  timers[0].fn(); await new Promise(r=>setTimeout(r,5))
  assert.equal(reads,before+1,'shown: the poll reads again')
  stop()
})

test('status squares render through the Desktop SDK CardCountSquare when it exists', async()=>{
  const calls=[]
  const CardCountSquare=({category,count})=>{ calls.push([category,count]); return React.createElement('i',{'data-sdk-square':category},count) }
  const Q=await loadPlugin({CardCountSquare})
  const snap={...fixture,lanes:{work:{running:2,not_ready:1,blocked_on_you:3,open:6}},status:{you:4,gate:2,triage:1}}
  const view=await mount(React.createElement(Q.Lanes,{ctx:{rest:async()=>snap}}))
  try {
    const strip=view.container.querySelector('.hermes-lanes-mini')
    assert.deepEqual([...strip.querySelectorAll('[data-status] [data-sdk-square]')].map(n=>[n.dataset.sdkSquare,n.textContent]),
      [['red','4'],['violet','2'],['outline','1']])
    assert.deepEqual([...strip.querySelectorAll('[data-lane-pill=work] [data-sdk-square]')].map(n=>[n.dataset.sdkSquare,n.textContent]),
      [['red','3'],['blue','2'],['outline','1']])
    assert.equal(strip.querySelector('[data-card-colour]'),null,'no local fallback when the SDK has the shared square')
  } finally { await view.close() }
})
