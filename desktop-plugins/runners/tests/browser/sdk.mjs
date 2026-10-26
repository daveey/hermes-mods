import React from 'react'
import {Popover as P,ContextMenu as C} from 'radix-ui'
export {useQuery,useQueryClient} from '@tanstack/react-query'
const atom=v=>({get:()=>v,subscribe:()=>()=>{}})
export const route={connectionId:'example',mode:'remote',profile:'alias',targetProfile:'example'}
export const host={state:{gateways:atom([]),connectionId:atom('example'),profile:atom('example')},profileRoutes:async()=>[route]}
export const useValue=a=>React.useSyncExternalStore(a.subscribe,a.get,a.get)
export const STATUSBAR_AREAS={right:'statusBar.right'},PALETTE_AREA='palette'
export const StatusDot=({tone,...p})=>React.createElement('span',{'data-tone':tone,...p})
export const Popover=P.Root,PopoverTrigger=P.Trigger,PopoverContent=React.forwardRef((p,r)=>React.createElement(P.Portal,null,React.createElement(P.Content,{...p,ref:r,className:'popover'})))
export const ContextMenu=C.Root,ContextMenuTrigger=C.Trigger,ContextMenuContent=C.Content,ContextMenuItem=C.Item,ContextMenuSeparator=C.Separator
