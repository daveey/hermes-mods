import { createElement as h } from 'react'
import { Popover as P, Dialog as D } from 'radix-ui'
export { useQuery, useQueryClient } from '@tanstack/react-query'
export const Popover = P.Root, PopoverTrigger = P.Trigger
export const PopoverContent = props => h(P.Portal, null, h(P.Content, props))
export const Dialog = D.Root, DialogTitle = D.Title
export const DialogContent = props => h(D.Portal, null, h(D.Content, { ...props, style: { position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)' } }, props.children, h(D.Close, { style: { position: 'absolute', right: 8, top: 8 }, 'aria-label': 'Close' }, '×')))
export const STATUSBAR_AREAS = { right: 'statusBar.right' }
export const PALETTE_AREA = 'palette'
export const useValue = atom => atom.get()
export const host = { state: { connectionId: { get: () => 'hermes-hub' }, gateways: { get: () => [{ id: 'hermes-hub', kind: 'ssh' }] } }, onEvent: () => () => {}, openSession: async (...args) => window.opened.push(args) }
