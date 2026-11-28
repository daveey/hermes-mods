// Fail-closed comparison of the bundled npm graph with Desktop's pnpm graph.
//
// Every package instance esbuild actually bundled (from the metafile) is
// reached through the import edges esbuild actually resolved. Each edge
// parent -> child must exist in Desktop's pnpm-lock.yaml (importer
// apps/desktop, then snapshots), and the installed child must have Desktop's
// locked version AND integrity for that exact edge. Anything unmapped, absent
// or different is an error. Pure function: tests feed it mutated inputs.
const IMPORTER='apps/desktop'
// scanner.ts itself imports these to build the remark processor that
// Streamdown builds internally; they are owned by Desktop's Streamdown.
export const SCANNER_OWNERS={unified:'streamdown','remark-parse':'streamdown'}

const instanceOf=file=>/^(.*node_modules\/(?:@[^/]+\/)?[^/]+)\//.exec(file)?.[1] ?? null
const plainVersion=v=>v.split('(')[0]

export function desktopGraphCheck({metafile,npmLock,desktopLock}) {
 const errors=[], packages=new Map(), importer=desktopLock?.importers?.[IMPORTER]
 if(!importer) return {errors:[`Desktop lock has no importer ${IMPORTER}`],packages:[]}
 const npm=inst=>npmLock?.packages?.[inst]
 // Desktop keys a dependency by its install name (an alias when aliased);
 // npm records the real package name in `name` for aliased installs.
 const dirName=inst=>inst.slice(inst.lastIndexOf('node_modules/')+13)
 const nameOf=dirName
 const importerDeps={...importer.devDependencies,...importer.optionalDependencies,...importer.dependencies}
 // Desktop's dependency value for `name` under a parent (importer or snapshot).
 const lookup=(parent,name)=>{
  if(parent===IMPORTER) return importerDeps[name]?.version
  const s=desktopLock.snapshots?.[parent]
  return s?.dependencies?.[name] ?? s?.optionalDependencies?.[name]
 }
 // edges: parent instance (or root label) -> child instances, from esbuild's
 // resolution. The scan path is every package with bytes in the bundle;
 // packages parsed but entirely tree-shaken are reported, not shipped.
 const shipped=new Set()
 for(const output of Object.values(metafile.outputs ?? {})) for(const [file,info] of Object.entries(output.inputs ?? {})) if(info.bytesInOutput>0 && instanceOf(file)) shipped.add(instanceOf(file))
 const edges=new Map(), bundled=new Set(), shaken=new Set()
 for(const [file,input] of Object.entries(metafile.inputs)) {
  const from=instanceOf(file) ?? (file.startsWith('desktop:')?'<desktop>':file)
  if(instanceOf(file)) (shipped.has(from)?bundled:shaken).add(from)
  for(const imp of input.imports ?? []) {
   if(imp.external) { if(!/^node:/.test(imp.path)) errors.push(`external import ${imp.path} from ${file}`); continue }
   const to=instanceOf(imp.path)
   if(!to || to===from || !shipped.has(to)) continue
   if(!edges.has(from)) edges.set(from,new Set())
   edges.get(from).add(to)
  }
 }
 for(const inst of bundled) shaken.delete(inst)
 const queue=[], seen=new Set()
 function visit(parentLabel,parentKey,child) {
  const name=nameOf(child), meta=npm(child)
  let value=lookup(parentKey,name)
  if(!meta) { errors.push(`${child}: not in package-lock.json`); return }
  if(value===undefined) { errors.push(`${parentLabel} -> ${name}: edge absent from Desktop lock (${parentKey})`); return }
  let lockName=name
  const alias=/^(@?[^@(]+)@([^(].*)$/.exec(value)
  if(alias) { lockName=alias[1]; value=alias[2] }
  if(/^(?:link|file|workspace|git|https?):/.test(value)) { errors.push(`${parentLabel} -> ${name}: unsupported Desktop reference ${value}`); return }
  const key=`${lockName}@${value}`, version=plainVersion(value)
  if((meta.name ?? name)!==lockName) errors.push(`${child}: installs ${meta.name ?? name} but Desktop locks ${lockName} for ${parentLabel} -> ${name}`)
  const integrity=desktopLock.packages?.[`${lockName}@${version}`]?.resolution?.integrity
  if(meta.version!==version) errors.push(`${child}: version ${meta.version} but Desktop locks ${version} for ${parentLabel} -> ${name}`)
  if(!integrity || meta.integrity!==integrity) errors.push(`${child}: integrity ${meta.integrity} but Desktop locks ${integrity} for ${lockName}@${version}`)
  if(!packages.has(child)) packages.set(child,{path:child,name:lockName,version:meta.version,integrity:meta.integrity,desktop:new Set()})
  packages.get(child).desktop.add(key)
  if(!seen.has(`${child}|${key}`)) { seen.add(`${child}|${key}`); queue.push([child,key]) }
 }
 for(const [root,children] of edges) {
  if(bundled.has(root) || shaken.has(root)) continue
  for(const child of children) {
   if(root==='<desktop>') visit('Desktop source',IMPORTER,child)
   else if(root==='scanner.ts' && SCANNER_OWNERS[nameOf(child)]) {
    const owner=SCANNER_OWNERS[nameOf(child)], ownerValue=lookup(IMPORTER,owner)
    if(!ownerValue) { errors.push(`scanner.ts -> ${nameOf(child)}: Desktop importer lacks owner ${owner}`); continue }
    visit(`scanner.ts (as ${owner})`,`${owner}@${ownerValue}`,child)
   } else errors.push(`${root} -> ${nameOf(child)}: root import without a Desktop owner`)
  }
 }
 while(queue.length) {
  const [inst,key]=queue.shift()
  for(const child of edges.get(inst) ?? []) visit(inst,key,child)
 }
 for(const inst of bundled) if(!packages.has(inst)) errors.push(`${inst}: bundled but not reached through any Desktop-locked edge`)
 return {errors,tree_shaken:[...shaken].sort(),packages:[...packages.values()].sort((a,b)=>a.path.localeCompare(b.path)).map(p=>({...p,desktop:[...p.desktop].sort()}))}
}
