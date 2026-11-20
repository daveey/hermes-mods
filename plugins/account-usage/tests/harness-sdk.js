// Synthetic SDK adapter; actual plugin.js and real React rendered in Chromium.
import React, {useSyncExternalStore, createContext, useContext} from 'react'
export function atom(value) { const listeners=new Set(); return {get:()=>value,set(v){value=v;listeners.forEach(f=>f())},subscribe(f){listeners.add(f);f();return()=>listeners.delete(f)}} }
export const host={navigate(p){(window.__nav||(window.__nav=[])).push(p)},state:Object.fromEntries(Object.entries({focusedSessionId:'runtime-a',focusedSessionOwner:{connectionId:'local',profile:'default'},focusedSessionProfile:'default',connectionId:'local',profile:'default',model:'same-model'}).map(([k,v])=>[k,atom(v)])),request:async()=>({providers:[{slug:'anthropic',usage:{accounts:[{id:'inventory-A',label:'Account A',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]},{id:'inventory-B',label:'Account B',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]},{id:'inventory-cooling',label:'Cooling account',state:'limited',limit_kind:'cooldown',reason:'rate_limit',cooldown_until:'2099-01-01T00:00:00Z',windows:[{label:'Current session',used_percent:40,resets_at:'2099-01-01T00:00:00Z'}],resets_at:'2099-01-01T00:00:00Z'},{id:'inventory-spent',label:'Spent account',state:'limited',limit_kind:'quota',reason:'quota_window',cooldown_until:null,model_cooldowns:[{model:'claude-fable-5-1',until:'2099-01-02T00:00:00Z'},{model:'claude-opus-5-5',until:'2099-01-02T00:00:00Z'},{model:'claude-sonnet-5-5',until:'2099-01-02T00:00:00Z'}],windows:[{label:'Current session',used_percent:100,resets_at:'2099-01-02T00:00:00Z'}],resets_at:'2099-01-02T00:00:00Z'}]}},{slug:'openai-codex',usage:{accounts:[{id:'inventory-GPT',label:'GPT account',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]},{id:'inventory-dead',label:'Dead account',state:'unavailable',reason:'auth',windows:[]},{id:'inventory-expired',label:'Expired account',state:'limited',limit_kind:'cooldown',reason:'auth',cooldown_until:'2099-01-01T00:00:00Z',resets_at:'2099-01-01T00:00:00Z',windows:[]}]}}]})}
const _request=host.request
host.request=async (...args)=>{
 if(window.fixture?.requestFail) throw new Error('fixture: model.options failed')
 const payload=await _request(...args)
 const f=window.fixture||{}
 if(f.extra && payload?.providers){
  // Claude dead grant (core: no in-app Anthropic login) and a Codex row core drops when its grant dies.
  // claudeNewListed: usage already lists the row under its new account id (stale "ready").
  payload.providers[0].usage.accounts.push(f.claudeRevived || f.claudeNewListed
   ? {id:'inventory-claude-new',label:'Claude dead',state:'ready',windows:[]}
   : {id:'inventory-claude-dead',label:'Claude dead',state:'unavailable',reason:'auth',windows:[]})
  if(f.reviveVanished) payload.providers[1].usage.accounts.push({id:'inventory-vanish',label:'Vanishing account',state:'ready',windows:[]})
  else if(!f.vanished) payload.providers[1].usage.accounts.push({id:'inventory-vanish',label:'Vanishing account',state:'limited',limit_kind:'cooldown',reason:'auth',cooldown_until:'2099-01-01T00:00:00Z',resets_at:'2099-01-01T00:00:00Z',windows:[]})
 }
 // What an unbound login beside a surviving manual row produces: a second row, the dead one unchanged.
 if(f.duplicate && payload?.providers) payload.providers[1].usage.accounts.push({id:'inventory-dead-duplicate',label:'Dead account',state:'ready',windows:[]})
 if(payload?.providers){
  for(const p of payload.providers) for(const a of p.usage?.accounts||[]){
   if((a.id==='inventory-dead'&&f.reviveDead)||(a.id==='inventory-expired'&&(f.reviveDead||f.reviveExpired))){ a.state='ready'; a.reason=null; a.limit_kind=null }
  }
 }
 return payload
}
export const useValue=a=>useSyncExternalStore(a.subscribe,a.get)
export const cn=(...x)=>x.filter(Boolean).join(' ')
export const STATUSBAR_AREAS={right:'statusBar.right'}
const Context=createContext(null)
export const Popover=({children,...props})=>React.createElement(Context.Provider,{value:props},children)
// Like Radix: the child's own onClick runs first and a default-prevented click skips the toggle.
export function PopoverTrigger({children}) {const p=useContext(Context);return React.cloneElement(children,{onClick:e=>{children.props.onClick?.(e);if(!e.defaultPrevented)p.onOpenChange(!p.open)}})}
// Like Radix's DismissableLayer: Escape anywhere closes an open popover.
export function PopoverContent({children,style,onMouseEnter,onMouseLeave}) {const p=useContext(Context);React.useEffect(()=>{if(!p.open)return;const k=e=>{if(e.key==='Escape'&&!e.defaultPrevented)p.onOpenChange(false)};document.addEventListener('keydown',k);return()=>document.removeEventListener('keydown',k)},[p.open]);return p.open?React.createElement('section',{'aria-label':'Account usage popover',style,onMouseEnter,onMouseLeave},children):null}
