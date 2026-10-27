import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'
import {Runners,CSS,projectFleet,projectNodes,bucketCounts} from '../../plugin.js'
import {route} from './sdk.mjs'
import fixtures from '../../../../plugins/runners/contracts/fixtures.json'
const params=new URLSearchParams(location.search), now=Date.now()
const iso=d=>new Date(now+d).toISOString()
const s=structuredClone(fixtures.InventoryResponse)
s.meta.observed_at=iso(-1000);s.meta.expires_at=iso(120000);s.meta.freshness='fresh'
s.data.hosts=[{...structuredClone(fixtures.Host),display_name:'Build node',os:'linux',arch:'x86_64'},{...structuredClone(fixtures.Host),id:'host_0000000000000002',resource_id:'resource_0000000000000002',display_name:'Quartz empty',os:'macos',arch:'arm64'}]
s.data.scopes=[{...s.data.scopes[0]}, {...s.data.scopes[0],id:'scope_0000000000000002',name:'example/secondary'}]
const hold=kind=>({kind,source:({maintenance:'maintenance_marker',drained:'systemd',disk_pressure:'admission_journal',root_headroom:'runner_journal'})[kind],state:'held',freshness:'fresh',observed_at:iso(-2000),expires_at:iso(60000),recorded_at:null})
const template=s.data.runners[0]
s.data.runners=Array.from({length:57},(_,i)=>({...structuredClone(template),id:`runner_${(i+1).toString(16).padStart(16,'0')}`,provider_runner_id:String(i+1),display_name:i===0?'Quartz runner':i<12?`Mapped runner ${String(i+1).padStart(2,'0')}`:`Unassigned registration ${String(i-11).padStart(2,'0')}`,host_id:i<12?s.data.hosts[0].id:null,scope_id:s.data.scopes[i>=42?1:0].id,registration:i>=12?'unknown':'online',activity:i===0?'busy':i<12?'idle':'unknown',readiness:i<4?'pass':'not_checked',operational_evidence:i>=4&&i<12?[hold(['drained','maintenance','disk_pressure','root_headroom'][(i-4)%4])]:[]}))
s.meta.provenance=[...s.data.runners.map(r=>({source_id:r.id,state:'fresh',observed_at:iso(-1000),expires_at:iso(60000),observation_ids:['obs_0000000000000001']})),...s.data.hosts.map(h=>({source_id:h.resource_id,state:'fresh',observed_at:iso(-2000),expires_at:iso(60000),observation_ids:['obs_0000000000000002']}))]
// Backend-faithful readiness causes (merged readiness_evidence contract): a fresh
// hold is a failed required check the owner reports as hold-explained; a stopped
// service is explained only by a systemd drain. Disk warnings stay independent.
const causes=(r,extra={})=>{const e=r.operational_evidence[0],key=`operational:${e.kind}:${e.source}`,failed=e.kind==='drained'?['service',key]:[key]
 return {required:['disk','service','tool:git',key],passed:e.kind==='drained'?['tool:git']:['service','tool:git'],failed,warned:['disk'],hold_explained_failures:failed,observed_at:iso(-2000),expires_at:iso(60000),freshness:'fresh',...extra}}
for(const r of s.data.runners.slice(4,12)){r.readiness='fail';r.readiness_evidence=causes(r)}
if(params.has('cases')) {
  const independent=s.data.runners[4].readiness_evidence;independent.required.push('tool:xcode');independent.failed.push('tool:xcode')
  s.data.runners[5].operational_evidence=[];s.data.runners[5].readiness='warn';s.data.runners[5].readiness_evidence={...causes({operational_evidence:[{kind:'maintenance',source:'maintenance_marker'}]}),failed:[],hold_explained_failures:[],passed:['service','tool:git']}
  s.data.runners[6].operational_evidence[0].expires_at=iso(-1);s.data.runners[6].readiness='not_checked';delete s.data.runners[6].readiness_evidence
  s.data.runners[7].freshness='stale'
  s.data.runners[8].registration='offline';s.data.runners[8].activity='busy'
  s.data.runners[9].activity='busy'
  s.data.runners[10].registration='offline'
  s.data.runners[11].operational_evidence=[hold('maintenance')];s.data.runners[11].readiness_evidence={...causes(s.data.runners[11]),failed:['service','operational:maintenance:maintenance_marker'],passed:['tool:git'],hold_explained_failures:['operational:maintenance:maintenance_marker']}
}
s.meta.coverage=s.data.scopes.map(scope=>({scope_id:scope.id,state:'complete',pagination_complete:true,enumerated_count:s.data.runners.filter(r=>r.scope_id===scope.id).length,provider_total_count:s.data.runners.filter(r=>r.scope_id===scope.id).length}))
if(params.has('long'))for(let i=3;i<70;i++)s.data.hosts.push({...s.data.hosts[0],id:`host_${i.toString(16).padStart(16,'0')}`,resource_id:`resource_${i.toString(16).padStart(16,'0')}`,display_name:`Extra node ${i}`})
window.entries=new Map();window.calls=[]
window.fixture=s
window.projection=()=>{const sources=[{route,snapshot:s}],fleet=projectFleet(sources);return {counts:bucketCounts(fleet.rows),nodes:projectNodes(sources,fleet.rows),rows:fleet.rows}}
const ctx={register:e=>{window.entries.set(e.id,e);return()=>window.entries.delete(e.id)},rest:async(path,options)=>{
 window.calls.push({path,options})
 if(path.includes('/enrollment'))return {contract_version:1,profile:'example',enrolled:true,capabilities:['inventory','inspect','refresh']}
 if(path.includes('/runners/')){const id=path.split('/runners/')[1].split('?')[0],runner=s.data.runners.find(r=>r.id===id);return {...s,data:{...structuredClone(fixtures.RunnerDetail),runner,jobs:[],observed_jobs:[]}}}
 return s
}}
const style=document.createElement('style');style.textContent=CSS;document.head.append(style)
if(params.get('theme')==='light')document.body.classList.add('light')
if(params.has('custom')){document.body.style.setProperty('--ui-accent','#8064db');document.body.style.setProperty('--warning','#a66b00')}
createRoot(document.getElementById('app')).render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false}}})},React.createElement(Runners,{ctx})))
