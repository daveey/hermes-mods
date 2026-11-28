// Reproducible build of scanner.mjs from EXACT committed Desktop source.
//
// Every core input is read as a Git blob of HERMES_CORE_COMMIT (default: HEAD
// of HERMES_CORE_GIT), never from a working tree, so manifest.core_commit is
// bound to the hashed bytes. The bundled npm graph must equal Desktop's own
// pnpm-lock.yaml graph at that commit, edge by edge (version AND integrity),
// or the build fails. See README.md.
import {build} from 'esbuild'
import ts from 'typescript'
import {parse as parseYaml} from 'yaml'
import {readFileSync,writeFileSync,readdirSync} from 'node:fs'
import {dirname,join} from 'node:path'
import {fileURLToPath} from 'node:url'
import {createHash} from 'node:crypto'
import {execFileSync} from 'node:child_process'
import {desktopGraphCheck} from './lockgraph.mjs'

const here=dirname(fileURLToPath(import.meta.url)); process.chdir(here)
const repo=process.env.HERMES_CORE_GIT ?? `${process.env.HOME}/.hermes/hermes-agent`
const git=(...args)=>execFileSync('git',['-C',repo,...args],{encoding:'utf8',maxBuffer:1<<28})
const commit=git('rev-parse','--verify',`${process.env.HERMES_CORE_COMMIT ?? 'HEAD'}^{commit}`).trim()
const hashes={}; const hash=s=>createHash('sha256').update(s).digest('hex')
// Exact committed bytes of one core path; recorded in the manifest.
function source(path) {
 const bytes=execFileSync('git',['-C',repo,'cat-file','blob',`${commit}:${path}`],{maxBuffer:1<<28})
 const s=bytes.toString('utf8')
 if(!Buffer.from(s,'utf8').equals(bytes)) throw Error(`Core input is not valid UTF-8: ${path}`)
 hashes[path]=hash(bytes); return s
}
const LIB='apps/desktop/src/lib/', MARKDOWN_TEXT='apps/desktop/src/components/assistant-ui/markdown-text.tsx'
// Select ORIGINAL top-level AST declarations by name, plus exactly the import
// specifiers those declarations reference. No alternate implementation or
// handwritten browser shim; whole-module hashes are recorded.
function select(path,names,{withImports=false,exports=false}={}) {
 const text=source(path), ast=ts.createSourceFile(path,text,ts.ScriptTarget.Latest,true,path.endsWith('x')?ts.ScriptKind.TSX:ts.ScriptKind.TS)
 const declared=s=>ts.isVariableStatement(s)?s.declarationList.declarations.map(d=>d.name.text):[s.name?.text]
 const selected=ast.statements.filter(s=>declared(s).some(n=>names.includes(n)))
 if(selected.length!==names.length || selected.some(s=>declared(s).length!==1)) throw Error(`Declaration selection drift: ${path}`)
 const used=new Set(); const walk=n=>{ if(ts.isIdentifier(n)) used.add(n.text); ts.forEachChild(n,walk) }; selected.forEach(walk)
 let imports=''
 if(withImports) for(const s of ast.statements) {
  if(!ts.isImportDeclaration(s) || s.importClause?.isTypeOnly || !s.importClause) continue
  const from=s.moduleSpecifier.text, c=s.importClause, keep=[]
  if(c.name && used.has(c.name.text)) imports+=`import ${c.name.text} from ${JSON.stringify(from)}\n`
  if(c.namedBindings && ts.isNamespaceImport(c.namedBindings) && used.has(c.namedBindings.name.text)) imports+=`import * as ${c.namedBindings.name.text} from ${JSON.stringify(from)}\n`
  if(c.namedBindings && ts.isNamedImports(c.namedBindings)) for(const e of c.namedBindings.elements) if(!e.isTypeOnly && used.has(e.name.text)) keep.push(e.propertyName?`${e.propertyName.text} as ${e.name.text}`:e.name.text)
  if(keep.length) imports+=`import {${keep.join(', ')}} from ${JSON.stringify(from)}\n`
 }
 return imports+selected.map(s=>s.getText(ast)).join('\n')+(exports?`\nexport {${names.join(', ')}}\n`:'\n')
}
const result=await build({entryPoints:['scanner.ts'],outfile:'scanner.mjs',write:false,bundle:true,platform:'node',target:'node18',format:'esm',legalComments:'eof',metafile:true,plugins:[{name:'desktop-source',setup(b){
 b.onResolve({filter:/^@\/components\/assistant-ui\/markdown-text$/},()=>({path:MARKDOWN_TEXT,namespace:'desktop'}))
 b.onResolve({filter:/^@\/lib\//},a=>({path:LIB+a.path.slice(6)+'.ts',namespace:'desktop'}))
 b.onResolve({filter:/^@\//},a=>{ throw Error(`Unmapped Desktop alias import: ${a.path}`) })
 // Relative imports inside core sources would otherwise resolve against
 // parser/ (no such import exists at the pinned commit). Refuse them.
 b.onResolve({filter:/^\.\.?\//},a=>{ if(a.namespace==='desktop') throw Error(`Relative import in core source ${a.importer}: ${a.path}`) })
 b.onLoad({filter:/.*/,namespace:'desktop'},a=>{
  let contents
  if(a.path===LIB+'media.ts') contents=select(a.path,['MEDIA_BY_EXT','pathExtension','mediaInfo','mediaKind','mediaMarkdownHref'])
  else if(a.path===LIB+'preview-targets.ts') contents=select(a.path,['PREVIEW_MARKDOWN_RE','stripPreviewTargets','previewMarkdownHref'])
  // The renderer's actual wiring: math plugin, remark list, preprocess+repair.
  else if(a.path===MARKDOWN_TEXT) contents=select(a.path,['mathPlugin','REMARK_PLUGINS','preprocessWithTailRepair'],{withImports:true,exports:true})
  else contents=source(a.path)
  // Bare npm specifiers resolve from this directory's verified node_modules.
  return {contents,loader:'ts',resolveDir:here}
 })
}}]})
// Renderer wiring and dependency identity are compatibility inputs too.
source('apps/desktop/src/components/assistant-ui/transcript-directive.tsx')
source('apps/desktop/package.json')
const desktopLock=source('pnpm-lock.yaml')
// F2: the full bundled npm graph must equal Desktop's locked graph.
const graph=desktopGraphCheck({metafile:result.metafile,npmLock:JSON.parse(readFileSync('package-lock.json','utf8')),desktopLock:parseYaml(desktopLock),here})
if(graph.errors.length) { console.error(graph.errors.join('\n')); throw Error(`Bundled npm graph differs from Desktop pnpm-lock.yaml@${commit}: ${graph.errors.length} problem(s)`) }
// Nothing is written until every check above has passed.
if(result.outputFiles.length!==1) throw Error('Expected exactly one bundle output')
const bundle=result.outputFiles[0].contents
writeFileSync('scanner.mjs',bundle)
const modules=Object.entries(hashes).sort(([a],[b])=>a.localeCompare(b)).map(([path,sha256])=>({path,sha256}))
writeFileSync('manifest.json',JSON.stringify({schema:2,core_commit:commit,modules,bundle_sha256:hash(bundle),
 pipeline:['preprocessWithTailRepair (markdown-text.tsx): preprocessMarkdown then @assistant-ui/react-streamdown tailBoundedRemend; content repair refuses (only terminal ASCII whitespace removal is equivalent)',
  'parseMarkdownIntoBlocksCached (markdown-blocks.ts, marked lexer): reconstruction must equal CR-normalized text; earlier blocks parsed alone must have no doubt; the last block alone must yield a sole final directive',
  'remark-parse',
  'REMARK_PLUGINS (markdown-text.tsx): streamdown.defaultRemarkPlugins (gfm, codeMeta), remarkTranscriptDirectives, remarkSoftBreaks',
  'mathPlugin.remarkPlugin (markdown-text.tsx via katex-memo.ts): remark-math {singleDollarTextMath: true}, appended last as Streamdown 2.7.0 does'],
 build:{esbuild:JSON.parse(readFileSync('node_modules/esbuild/package.json','utf8')).version,typescript:ts.version},
 dependencies:graph.packages,tree_shaken_packages:graph.tree_shaken},null,2)+'\n')
// Ship original license texts for every bundled npm package instance, keyed by
// its full node_modules path, plus build tools.
let notices='Third-party licenses (bundled source and build tools)\n'
for(const path of [...graph.packages.map(p=>p.path),'node_modules/esbuild','node_modules/typescript','node_modules/yaml']) {
 const root=join(here,path), pkg=JSON.parse(readFileSync(join(root,'package.json'),'utf8'))
 let texts=readdirSync(root).filter(n=>/^licen[sc]e(?:\.|$)/i.test(n)).sort().map(n=>readFileSync(join(root,n),'utf8'))
 if(!texts.length) {
  if(pkg.name!=='remark-math') throw Error(`Missing license: ${path}`)
  texts=[readFileSync('remark-math-LICENSE','utf8')]
 }
 notices+=`\n===== ${path} (${pkg.name}@${pkg.version}, ${pkg.license}) =====\n`+texts.join('\n')
}
writeFileSync('THIRD-PARTY-LICENSES.txt',notices)
console.log(`Built scanner.mjs from ${commit}; ${modules.length} hashed core inputs; ${graph.packages.length} bundled packages match Desktop's lock`)
