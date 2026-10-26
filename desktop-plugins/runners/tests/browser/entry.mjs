import React from 'react'
import {createRoot} from 'react-dom/client'
import {QueryClient,QueryClientProvider} from '@tanstack/react-query'
import {Runners,CSS} from '../../plugin.js'
import fixtures from '../../../../plugins/runners/contracts/fixtures.json'
const s=structuredClone(fixtures.InventoryResponse)
s.meta.observed_at=new Date().toISOString();s.meta.expires_at=new Date(Date.now()+120000).toISOString();s.meta.freshness='fresh'
s.data.hosts=[{...fixtures.Host,display_name:'Build node',os:'linux',arch:'x86_64'},{...fixtures.Host,id:'host_0000000000000002',display_name:'Quartz empty',os:'macos',arch:'arm64'}]
s.data.runners[0].host_id=s.data.hosts[0].id
s.data.runners[0].display_name='Quartz runner'
s.data.runners.push({...s.data.runners[0],id:'run_0000000000000002',provider_runner_id:'1234567',host_id:null,display_name:'Unmapped runner'})
if(new URLSearchParams(location.search).has('long')) {
  for(let i=3;i<70;i++)s.data.hosts.push({...s.data.hosts[0],id:`host_${i.toString(16).padStart(16,'0')}`,display_name:`Extra node ${i}`})
}
window.entries=new Map();window.calls=[]
const detail=structuredClone(fixtures.InspectResponse)
detail.meta=structuredClone(s.meta);detail.data.runner=structuredClone(s.data.runners[0])
const ctx={register:e=>{window.entries.set(e.id,e);return()=>window.entries.delete(e.id)},rest:async(path,options)=>{window.calls.push({path,options});return path.includes('/enrollment')?{contract_version:1,profile:'example',enrolled:true,capabilities:['inventory','inspect','refresh']}:path.includes('/runners/')?detail:s}}
const style=document.createElement('style');style.textContent=CSS;document.head.append(style)
if(new URLSearchParams(location.search).get('theme')==='light')document.body.classList.add('light')
createRoot(document.getElementById('app')).render(React.createElement(QueryClientProvider,{client:new QueryClient({defaultOptions:{queries:{retry:false}}})},React.createElement(Runners,{ctx})))
