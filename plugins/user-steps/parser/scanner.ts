import {readFileSync} from 'node:fs'
import {unified} from 'unified'
import remarkParse from 'remark-parse'
import {defaultRemarkPlugins} from 'streamdown'
import {preprocessMarkdown} from '@/lib/markdown-preprocess'
import {remarkTranscriptDirectives, segmentTranscriptDirectives} from '@/lib/transcript-directives'
import {remarkSoftBreaks} from '@/lib/remark-soft-breaks'

const processor = unified().use(remarkParse).use(Object.values(defaultRemarkPlugins)).use(remarkTranscriptDirectives).use(remarkSoftBreaks)
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
export function scan(reply:unknown):any {
 if(typeof reply !== 'string' || reply.length>200000 || reply.includes('\uE000') || reply.includes('\uE001')) return null
 const text=preprocessMarkdown(reply)
 const tree:any=processor.runSync(processor.parse(text))
 const found:any[]=[]; let doubt=false
 function visit(n:any, ancestors:any[]) {
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
 if(doubt || found.length!==1) return null
 const {d,node,ancestors}=found[0]
 if(d.name!=='user-step' || !strictAttrs(d.source)) return null
 const blocks=tree.children, last=blocks.at(-1)
 if(last?.type!=='paragraph' || ancestors.length!==2 || ancestors[1]!==last || last.children.length!==1 || last.children[0]!==node || node.value.trim()!==d.source) return null
 const offset=last.position?.start?.offset
 if(!Number.isInteger(offset) || text.slice(last.position.end.offset).trim()) return null
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
