// Consumer contract: real Python/API records feed the actual Session Panel renderer.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { register } from 'node:module'
register('data:text/javascript,' + encodeURIComponent(`
  export async function load(url, context, next) {
    return next(url, url.endsWith('/desktop-plugins/session-panel/plugin.js') ? {...context, format:'module'} : context)
  }
  export async function resolve(s, c, next) {
    const modules = {
      '@hermes/plugin-sdk': 'export const COMPOSER_AREAS={},host={},Codicon=null,DisclosureCaret=null,useValue=()=>{}',
      'react': 'export const useState=()=>{},useEffect=()=>{},useRef=()=>{}',
      'react/jsx-runtime': 'export const jsx=(type,props)=>({type,props}),jsxs=jsx'
    }
    return s in modules ? {url:'data:text/javascript,'+encodeURIComponent(modules[s]),shortCircuit:true} : next(s,c)
  }
`))
const { PersonWaitRows } = await import('../../../desktop-plugins/session-panel/plugin.js')
const records = JSON.parse(readFileSync(0, 'utf8'))
const element = PersonWaitRows({ records })
const rows = element.props.children
assert.equal(rows.length, records.length)
for (const [index, row] of rows.entries()) {
  const record = records[index]
  const anchor = row.props.children.find(child => child?.type === 'a')
  assert.ok(anchor, 'backend thread must render as an actual anchor')
  assert.equal(anchor.props.href, record.thread)
  assert.equal(anchor.props.children, record.contact)
  assert.match(row.props.children[0], record.state === 'verifying' ? /Verifying/ : /Waiting on/)
}
console.log(JSON.stringify({ validated: rows.length }))
