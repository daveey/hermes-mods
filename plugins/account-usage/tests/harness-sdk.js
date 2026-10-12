// Synthetic SDK adapter; actual plugin.js and real React rendered in Chromium.
import React, {useSyncExternalStore, createContext, useContext} from 'react'
export function atom(value) { const listeners=new Set(); return {get:()=>value,set(v){value=v;listeners.forEach(f=>f())},subscribe(f){listeners.add(f);f();return()=>listeners.delete(f)}} }
export const host={state:Object.fromEntries(Object.entries({focusedSessionId:'runtime-a',focusedSessionOwner:{connectionId:'local',profile:'default'},focusedSessionProfile:'default',connectionId:'local',profile:'default',model:'same-model'}).map(([k,v])=>[k,atom(v)])),request:async()=>({providers:[{slug:'anthropic',usage:{accounts:[{id:'inventory-A',label:'Account A',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]},{id:'inventory-B',label:'Account B',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]},{id:'inventory-cooling',label:'Cooling account',state:'limited',windows:[],resets_at:'2099-01-01T00:00:00Z'}]}},{slug:'openai-codex',usage:{accounts:[{id:'inventory-GPT',label:'GPT account',state:'ready',windows:[{label:'Current session',used_percent:24,resets_at:'2099-01-01T00:00:00Z'},{label:'Current week',used_percent:61,resets_at:'2099-01-03T00:00:00Z'}]}]}}]})}
export const useValue=a=>useSyncExternalStore(a.subscribe,a.get)
export const cn=(...x)=>x.filter(Boolean).join(' ')
export const STATUSBAR_AREAS={right:'statusBar.right'}
const Context=createContext(null)
export const Popover=({children,...props})=>React.createElement(Context.Provider,{value:props},children)
export function PopoverTrigger({children}) {const p=useContext(Context);return React.cloneElement(children,{onClick:()=>p.onOpenChange(!p.open)})}
export function PopoverContent({children}) {const p=useContext(Context);return p.open?React.createElement('section',{'aria-label':'Account usage popover'},children):null}
