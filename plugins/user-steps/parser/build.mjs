import {build} from 'esbuild'
import ts from 'typescript'
import {readFileSync,writeFileSync,readdirSync,existsSync} from 'node:fs'
import {resolve,relative,dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
import {execFileSync} from 'node:child_process'
const here=dirname(fileURLToPath(import.meta.url)); process.chdir(here)
const core=resolve(process.env.HERMES_CORE_ROOT ?? `${process.env.HOME}/.hermes/hermes-agent`)
const lib=join(core,'apps/desktop/src/lib')
const hashes={}; const hash=s=>createHash('sha256').update(s).digest('hex')
function source(path) { const s=readFileSync(path,'utf8'); hashes[relative(core,path)]=hash(s); return s }
// Select ORIGINAL top-level AST declarations by name. Do not execute media's
// browser/API/store imports: the preprocessing functions only need these pure
// declarations. No alternate implementation or handwritten browser shim.
function select(path,names) {
 const text=source(path), ast=ts.createSourceFile(path,text,ts.ScriptTarget.Latest,true)
 const selected=ast.statements.filter(s=>names.includes(s.name?.text) || (ts.isVariableStatement(s)&&s.declarationList.declarations.some(d=>names.includes(d.name.text))))
 if(selected.length!==names.length) throw Error(`Declaration selection drift: ${path}`)
 return selected.map(s=>s.getText(ast)).join('\n')
}
const result=await build({entryPoints:['scanner.ts'],outfile:'scanner.mjs',bundle:true,platform:'node',target:'node18',format:'esm',legalComments:'eof',metafile:true,plugins:[{name:'desktop-source',setup(b){
 b.onResolve({filter:/^@\/lib\//},a=>({path:a.path.slice(6)+'.ts',namespace:'desktop'}))
 b.onResolve({filter:/^@assistant-ui\/react-streamdown$/},()=>({path:join(here,'node_modules/@assistant-ui/react-streamdown/dist/preprocess.js')}))
 b.onLoad({filter:/.*/,namespace:'desktop'},a=>{
  a={...a,path:join(lib,a.path)}
  let contents
  if(a.path===join(lib,'media.ts')) contents=select(a.path,['MEDIA_BY_EXT','pathExtension','mediaInfo','mediaKind','mediaMarkdownHref'])
  else if(a.path===join(lib,'preview-targets.ts')) contents=select(a.path,['PREVIEW_MARKDOWN_RE','stripPreviewTargets','previewMarkdownHref'])
  else contents=source(a.path)
  return {contents,loader:'ts',resolveDir:here}
 })
}}]})
// Include renderer wiring in compatibility validation, not just imported modules.
source(join(core,'apps/desktop/src/components/assistant-ui/markdown-text.tsx'))
source(join(core,'apps/desktop/src/components/assistant-ui/transcript-directive.tsx'))
source(join(core,'apps/desktop/package.json'))
const modules=Object.entries(hashes).sort(([a],[b])=>a.localeCompare(b)).map(([path,sha256])=>({path,sha256}))
writeFileSync('manifest.json',JSON.stringify({schema:1,core_commit:execFileSync('git',['-C',core,'rev-parse','HEAD'],{encoding:'utf8'}).trim(),modules,bundle_sha256:hash(readFileSync('scanner.mjs')),pipeline:['preprocessMarkdown','remark-parse','streamdown.defaultRemarkPlugins (gfm, codeMeta)','remarkTranscriptDirectives','remarkSoftBreaks']},null,2)+'\n')
// Ship original license texts for every bundled npm package, plus build tools.
const packages=new Set(['esbuild','typescript'])
for(const name of Object.keys(result.metafile.inputs)) {
 const i=name.lastIndexOf('node_modules/'); if(i<0)continue
 const parts=name.slice(i+13).split('/'); packages.add(parts[0].startsWith('@')?parts.slice(0,2).join('/'):parts[0])
}
let notices='Third-party licenses (bundled source and build tools)\n'
for(const name of [...packages].sort()) {
 const root=join(here,'node_modules',name), pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'))
 const licenses=readdirSync(root).filter(n=>/^licen[sc]e(?:\.|$)/i.test(n))
 let texts=licenses.map(n=>readFileSync(join(root,n),'utf8'))
 if(!texts.length) {
  if(name!=='remark-math') throw Error(`Missing license: ${name}`)
  texts=[readFileSync('remark-math-LICENSE','utf8')]
 }
 notices+=`\n===== ${name}@${pkg.version} (${pkg.license}) =====\n`+texts.join('\n')
}
writeFileSync('THIRD-PARTY-LICENSES.txt',notices)
console.log(`Built scanner.mjs; ${modules.length} hashed core modules; ${packages.size} package licenses`)
