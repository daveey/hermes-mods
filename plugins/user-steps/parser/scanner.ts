import {readFileSync} from 'node:fs'
import {unified} from 'unified'
import remarkParse from 'remark-parse'
// Original declarations selected from Desktop's markdown-text.tsx by build.mjs
// (TypeScript AST selection, not a copy): the renderer's own remark list, its
// math plugin instance and its unconditional preprocess + tail repair.
import {REMARK_PLUGINS, mathPlugin, preprocessWithTailRepair} from '@/components/assistant-ui/markdown-text'
import {parseMarkdownIntoBlocksCached} from '@/lib/markdown-blocks'
import {preprocessMarkdown} from '@/lib/markdown-preprocess'
import {segmentTranscriptDirectives} from '@/lib/transcript-directives'

// Streamdown 2.7.0 composes each block's remark chain as
// [...remarkPlugins, plugins.math.remarkPlugin] (no cjk plugin is configured).
const processor = unified().use(remarkParse).use(REMARK_PLUGINS).use([mathPlugin.remarkPlugin])
const parse = (text:string):any => processor.runSync(processor.parse(text))
function plain(n:any):string { return typeof n.value === 'string' ? n.value : (n.children ?? []).map(plain).join(' ') }
function words(n:any):number { return (plain(n).match(/[\p{L}\p{N}]+(?:['’-][\p{L}\p{N}]+)*/gu) ?? []).length }
// This is validation of the Desktop parser result, not another directive parser.
function strictAttrs(source:string):boolean {
 const open=source.indexOf('{'); if(open<0 || !source.endsWith('}')) return false
 let body=source.slice(open+1,-1), seen=new Set<string>()
 while(body.trim()) {
  const m=/^\s*([a-z][\w-]{0,63})=(?:"[^"]*"|'[^']*')(?:\s+|$)/i.exec(body)
  if(!m || seen.has(m[1].toLowerCase())) return false
  seen.add(m[1].toLowerCase()); body=body.slice(m[0].length)
 }
 return true
}
// Raw HTML Desktop renders through rehype-raw can open an element that wraps
// everything after it (an unclosed <a> turns the card into a blocked link).
// Only self-contained comments and void elements are tolerated.
const SAFE_HTML=/^(?:<!--(?:(?!-->)[\s\S])*-->|<(?:br|hr|img|wbr)\b[^<>]*>)$/i
// Every directive in a parsed tree; any `::` outside a cleanly segmented text
// node (math, code, html, link targets...) or any other raw HTML is doubt.
function directives(tree:any) {
 const found:any[]=[]; let doubt=false
 function visit(n:any, ancestors:any[]) {
  if(n.type==='html' && !SAFE_HTML.test(n.value.trim())) doubt=true
  if(n.type==='text' && n.value?.includes('::')) {
   const segments=segmentTranscriptDirectives(n.value)
   if(!segments) { doubt=true; return }
   for(const s of segments) {
    if(s.kind==='directive') found.push({d:s.directive,node:n,ancestors})
    else if(s.text.includes('::')) doubt=true
   }
  } else if(typeof n.value==='string' && n.value.includes('::')) doubt=true
  for(const c of n.children??[]) visit(c,[...ancestors,n])
 }
 visit(tree,[])
 return {found,doubt}
}
// The tree's only directive must be the sole content of its final top-level paragraph.
function finalDirective(tree:any):any {
 const {found,doubt}=directives(tree)
 if(doubt || found.length!==1) return null
 const {d,node,ancestors}=found[0], last=tree.children.at(-1)
 if(d.name!=='user-step' || !strictAttrs(d.source)) return null
 if(last?.type!=='paragraph' || ancestors.length!==2 || ancestors[1]!==last || last.children.length!==1 || last.children[0]!==node || node.value.trim()!==d.source) return null
 return {d,node,last}
}
export function scan(reply:unknown):any {
 if(typeof reply !== 'string' || reply.length>200000 || reply.includes('\uE000') || reply.includes('\uE001')) return null
 const text=preprocessMarkdown(reply)
 // Desktop renders preprocessWithTailRepair(reply) for finished replies too.
 // Whenever that repair changes the text, refuse: an ask is admitted only when
 // Desktop renders exactly the unrepaired preprocessed reply, so no repair can
 // create, move, extend or close an actionable directive.
 if(preprocessWithTailRepair(reply)!==text) return null
 const tree=parse(text), whole=finalDirective(tree)
 if(!whole) return null
 const {d,node,last}=whole, blocks=tree.children
 const offset=last.position?.start?.offset
 if(!Number.isInteger(offset) || text.slice(last.position.end.offset).trim()) return null
 // Desktop renders each marked block as an independent document (marked
 // normalizes CR/CRLF to LF). Parsed alone through the same chain, no block
 // but the last one may hold doubt (an earlier directive is already
 // impossible: the reply has exactly one `::`, checked below), and the last
 // block alone must still yield a sole final directive. Fence disagreements
 // between marked and micromark (e.g. a "```~" line) otherwise leave Desktop
 // rendering the ask inside a code block.
 const split=parseMarkdownIntoBlocksCached(text)
 if(!Array.isArray(split) || split.join('')!==text.replace(/\r\n|\r/g,'\n')) return null
 const content=split
 for(const block of content.slice(0,-1)) if(directives(parse(block)).doubt) return null
 // With a single `::`, the last block's directive is the same source.
 if(!finalDirective(parse(content.at(-1) as string))) return null
 // Reject hidden/malformed/additional source cues even if preprocessing removed them.
 if((reply.match(/::/g)??[]).length!==1) return null
 const previous=blocks.at(-2)
 const list=previous?.type==='list' && previous.children.length>=2 && previous.children.every((n:any)=>words(n)>=4)
 const heading=blocks.at(-3)
 const prose=previous?.type==='paragraph' && words(previous)>=25 && heading?.type==='heading' && /\bplan\b/i.test(plain(heading))
 if(!list && !prose) return null
 const plan=text.slice(0,offset).trimEnd()
 if(!plan.trim()) return null
 return {ask:{attrs:d.attrs,source:d.source,text:node.value},plan}
}
try { const input=JSON.parse(readFileSync(0,'utf8')); process.stdout.write(JSON.stringify(scan(input.reply))+'\n') }
catch { process.stdout.write('null\n') }
