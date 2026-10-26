import {chromium} from './node_modules/playwright/index.mjs'
import {writeFile} from 'node:fs/promises'
import assert from 'node:assert/strict'
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome',args:['--no-sandbox']})
const report={fixture:'Fictional 57 registrations; 45 explicitly unassigned. Actual plugin, React and Radix; approximated SDK theme/chrome.',errors:[],captures:[],checks:[],limitations:['OS-native IME candidate window and native palette shell not tested','Screen-reader walkthrough not performed']}
const check=(name,value)=>{assert.ok(value,name);report.checks.push(name)}
const collect=(name,value)=>{if(value)report.checks.push(name);else(report.failures??=[]).push(name)}
try{
const page=await browser.newPage({viewport:{width:900,height:900}})
page.on('pageerror',e=>report.errors.push(e.message))
const open=async(query='')=>{await page.goto('file://'+process.cwd()+'/index.html'+query);await page.locator('[data-slot=runners-chip]').click();await page.locator('[data-node-row]').first().waitFor()}
const input=page.getByRole('textbox',{name:'Search runners'}),state=page.getByRole('combobox',{name:'Runner state'})
await open()
check('57 registrations, exact partition 1/3/8/0/45',JSON.stringify(await page.evaluate(()=>window.projection().counts))===JSON.stringify({running:1,idle:3,held:8,failed:0,unknown:45}))
check('45 unassigned, mapped cohort 12 and empty physical node retained',await page.evaluate(()=>{const p=window.projection();return p.nodes.unassigned.length===45&&p.nodes.nodes.length===2&&p.nodes.nodes[0].registrationCount===12&&p.nodes.nodes[1].registrationCount===0}))
check('all nodes initially collapsed',await page.locator('[data-node-row][aria-expanded=true]').count()===0)
check('no detail prefetch',await page.evaluate(()=>window.calls.every(c=>!c.path.includes('/runners/'))))
await page.locator('[data-node-row]').first().focus();await page.keyboard.type('Quartz')
check('real type-ahead seeds editable input',await input.inputValue()==='Quartz')
await page.keyboard.press('ArrowLeft');await page.keyboard.press('ArrowLeft');await page.keyboard.type('X')
check('middle insertion retains caret and input identity',await input.inputValue()==='QuarXtz'&&await input.evaluate(e=>e===document.activeElement&&e.selectionStart===5))
await page.keyboard.press('Backspace');check('middle deletion retains suffix',await input.inputValue()==='Quartz')
const cdp=await page.context().newCDPSession(page)
await cdp.send('Input.imeSetComposition',{text:'界',selectionStart:1,selectionEnd:1})
check('Chrome IME composition preserves input focus',await input.evaluate(e=>e===document.activeElement))
await cdp.send('Input.insertText',{text:'界'})
check('Chrome IME commit retains suffix',await input.inputValue()==='Quar界tz')
await page.keyboard.press('Backspace')
await input.evaluate(e=>{window.originalInput=e;e.dispatchEvent(new CompositionEvent('compositionstart',{bubbles:true,data:'界'}));e.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',isComposing:true,bubbles:true}));e.dispatchEvent(new CompositionEvent('compositionend',{bubbles:true,data:'界'}))})
collect('composition key does not navigate or remount input',await input.evaluate(e=>e===window.originalInput&&e===document.activeElement&&e.value==='Quartz'))
// Recover focus only to collect independent checks; the failed gate stays fatal.
await input.focus();await input.evaluate(e=>e.setSelectionRange(4,4))
await page.keyboard.insertText('界');check('composition text insertion uses same input and caret',await input.inputValue()==='Quar界tz')
await page.keyboard.press('Escape');check('Escape clears search before closing',await input.inputValue()===''&&await page.locator('[data-runners-panel]').count()===1)
await page.locator('[data-node-row]').first().focus();await page.keyboard.press('Control+x')
check('modifier does not seed type-ahead',await input.inputValue()==='')
await page.keyboard.press('Control+/')
collect('modified slash does not hijack focus',await page.locator('[data-node-row]').first().evaluate(e=>e===document.activeElement))
await page.locator('[data-node-row]').first().focus()
await page.keyboard.press('ArrowRight');check('Right expands node',await page.locator('[data-node-row]').first().getAttribute('aria-expanded')==='true')
await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');await page.locator('.hermes-runners-detail').waitFor()
check('detail is lazy and owner routed',await page.evaluate(()=>window.calls.filter(c=>c.path.includes('/runners/')).length===1&&window.calls.filter(c=>c.path.includes('/runners/')).every(c=>c.options.connectionId==='example'&&c.path.includes('profile=example'))))
await page.keyboard.press('Escape');check('Escape closes detail only',await page.locator('.hermes-runners-detail').count()===0)
await page.locator('[data-runner-row]').first().click({button:'right'});await page.getByRole('menu').waitFor()
check('unsupported maintenance disabled in native menu',await page.getByRole('menuitem').filter({hasText:'Maintenance controls unavailable'}).getAttribute('data-disabled')!==null)
await page.keyboard.press('Escape')
// Radix restores focus after its close animation frame; wait (bounded) rather than sample once.
const menuFocus=await page.waitForFunction(()=>!document.querySelector('[role=menu]')&&document.querySelector('[data-runner-row]')===document.activeElement,null,{timeout:2000}).then(()=>true,()=>false)
check('native menu Escape focus round trip',menuFocus)
await page.keyboard.press('Shift+F10');await page.getByRole('menu').waitFor();await page.keyboard.press('Escape')
await page.evaluate(()=>{const e=[...window.entries.values()].find(e=>e.data.label.startsWith('Show runner:'));if(!e)throw Error('No runner entry');e.data.run()});await page.locator('.hermes-runners-detail').waitFor()
check('registered palette runner alias opens detail',await page.locator('.hermes-runners-detail').count()===1);await page.keyboard.press('Escape')
await state.focus();await page.keyboard.press('Home');await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
check('native state select keyboard selects Running',await state.inputValue()==='Running (Busy)')
for(const [filter,count] of [['Running (Busy)',1],['Idle',3],['Held/maintenance',8],['Failed',0],['Unknown/stale',45],['Checks passed + idle (Ready)',3],['Needs attention',53],['All',57]]){
 await state.selectOption(filter)
 check(`${filter}: scope and shown counts`,await page.locator('[data-filtered-cohort]').textContent()===(filter==='All'?'57 registrations':`57 registrations · ${count} shown`))
 const total=await page.locator('[data-registration-bar]').evaluateAll(es=>es.reduce((sum,e)=>sum+Number(e.getAttribute('aria-label').match(/^\d+/)[0]),0))
 check(`${filter}: bars sum to visible cohort`,total===count)
}
await page.getByRole('combobox',{name:'Gateway scope'}).selectOption({index:1})
check('native owner scope retains registration total',await page.locator('[data-filtered-cohort]').textContent()==='57 registrations')
await input.focus();await page.keyboard.press('Tab');check('Tab reaches native state select',await state.evaluate(e=>e===document.activeElement))
await page.locator('[data-node-row]').first().focus();await page.keyboard.press('?');await page.locator('[data-runners-help]').waitFor();await page.keyboard.press('Escape')
check('help Escape retains popover',await page.locator('[data-runners-panel]').count()===1)
await page.locator('[data-node-row]').first().focus();await page.keyboard.press('ArrowLeft');await page.keyboard.press('Space')
check('Space toggles focused node',await page.locator('[data-node-row]').first().getAttribute('aria-expanded')==='true')
await page.keyboard.press('ArrowLeft');await page.keyboard.press('Escape')
check('Escape returns focus to native status trigger',await page.locator('[data-runners-panel]').count()===0&&await page.locator('[data-slot=runners-chip]').evaluate(e=>e===document.activeElement))
for(const width of [900,640,390])for(const theme of ['dark','light'])for(const height of [900,480]){
 await page.setViewportSize({width,height});await open(`?theme=${theme}`)
 const metrics=await page.evaluate(()=>{const p=document.querySelector('[data-runners-panel]'),s=document.querySelector('.hermes-runners-scroll');return {page:document.documentElement.scrollWidth,panel:p.getBoundingClientRect().toJSON(),overflow:p.scrollWidth>p.clientWidth,scroll:s.scrollHeight>s.clientHeight}})
 check(`${theme} ${width}x${height}: no horizontal overflow and bounded panel`,metrics.page<=width&&!metrics.overflow&&metrics.panel.left>=0&&metrics.panel.right<=width&&metrics.panel.height<=height*.7+1)
 const path=`${theme}-${width}${height===480?'-short':''}.png`;await page.screenshot({path});report.captures.push({path,theme,width,height,metrics})
 await page.locator('[data-node-row]').filter({hasText:'Unassigned runners'}).click()
 check(`${theme} ${width}x${height}: full 45-row unassigned cohort`,await page.locator('[data-runner-row]').count()===45)
 const scrolled=await page.evaluate(()=>{const s=document.querySelector('.hermes-runners-scroll'),head=document.querySelector('.hermes-runners-head'),filter=document.querySelector('.hermes-runners-filters');const top=head.getBoundingClientRect().top;s.scrollTop=s.scrollHeight;return {stable:top===head.getBoundingClientRect().top,scroll:s.scrollTop>0,overflow:s.scrollWidth>s.clientWidth,filter:filter.getBoundingClientRect().bottom<innerHeight}})
 check(`${theme} ${width}x${height}: internal scroll keeps header/filter accessible`,scrolled.stable&&scrolled.scroll&&!scrolled.overflow&&scrolled.filter)
 if(width===390&&height===480){const path=`${theme}-390-short-expanded.png`;await page.screenshot({path});report.captures.push({path})}
}
await page.setViewportSize({width:900,height:900});await open('?cases=1')
check('all bucket cases include failure, warning, stale, expired hold and conflict',await page.evaluate(()=>{const p=window.projection();return p.counts.failed===2&&p.rows.some(r=>r.bucket==='failed'&&/Failed: Tool xcode/.test(r.reason)&&r.holds.length)&&p.rows.some(r=>r.bucket==='failed'&&/Failed: Service/.test(r.reason)&&r.maintenance)&&p.rows.some(r=>r.bucketLabel==='Degraded')&&p.rows.some(r=>r.bucketLabel==='Hold expired · awaiting evidence')&&p.rows.some(r=>r.bucketLabel==='Stale')&&p.rows.some(r=>r.bucketLabel==='Conflicting observations')&&p.rows.some(r=>r.bucket==='running'&&r.holds.length)}))
await page.locator('[data-node-row]').first().click();await page.screenshot({path:'all-cases.png'});report.captures.push({path:'all-cases.png'})
await state.selectOption('Failed');check('failed filter keeps independently failing held runner',await page.locator('[data-filtered-cohort]').textContent()==='57 registrations · 2 shown')
await page.locator('[data-runner-row]').first().click();await page.locator('.hermes-runners-detail').waitFor()
await page.getByText('Loading details…',{exact:true}).waitFor({state:'hidden'})
check('failed detail separates independent and hold-explained checks',/Failed \(independent\): Tool xcode/.test(await page.locator('.hermes-runners-detail').textContent())&&/Failed \(explained by current hold\)/.test(await page.locator('.hermes-runners-detail').textContent()))
await page.screenshot({path:'failed-detail.png'});report.captures.push({path:'failed-detail.png'})
for(const width of [900,640,390])for(const theme of ['dark','light']){
 await page.setViewportSize({width,height:900});await open(`?cases=1&theme=${theme}`);await state.selectOption('Failed')
 await page.locator('[data-node-row]').first().click();await page.locator('[data-runner-row]').first().click()
 await page.locator('.hermes-runners-detail').waitFor();await page.getByText('Loading details…',{exact:true}).waitFor({state:'hidden'})
 const metric=await page.locator('.hermes-runners-scroll').evaluate(e=>({scroll:e.scrollWidth,client:e.clientWidth}))
 collect(`${theme} ${width}: expanded evidence has no horizontal overflow`,metric.scroll<=metric.client)
 const path=`${theme}-${width}-detail.png`;await page.screenshot({path});report.captures.push({path,metric})
}
await page.setViewportSize({width:390,height:480});await open('?theme=light&custom=1');await page.screenshot({path:'custom-390-short.png'});report.captures.push({path:'custom-390-short.png'})
report.contrast=[]
for(const query of ['?theme=dark','?theme=light','?theme=light&custom=1']){
 await open(query)
 const ratios=await page.evaluate(()=>{
  const panel=document.querySelector('[data-runners-panel]'),style=getComputedStyle(panel)
  const rgb=color=>{const e=document.createElement('span');e.style.color=color;panel.append(e);const result=getComputedStyle(e).color.match(/[\d.]+/g).slice(0,3).map(Number);e.remove();return result}
  const lum=c=>rgb(c).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4}).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0)
  const bg=lum(style.getPropertyValue('--ui-bg-elevated'))
  const ratio=c=>{const l=lum(c);return (Math.max(l,bg)+.05)/(Math.min(l,bg)+.05)}
  return {text:ratio(style.getPropertyValue('--foreground')),secondary:ratio(style.getPropertyValue('--ui-text-secondary')),focus:ratio(style.getPropertyValue('--ui-accent')),held:ratio(style.getPropertyValue('--warning')||'#b87918'),failure:ratio(style.getPropertyValue('--destructive')||'#c74949')}
 })
 report.contrast.push({query,ratios})
 collect(`${query}: text contrast at least 4.5:1`,ratios.text>=4.5&&ratios.secondary>=4.5)
 collect(`${query}: focus/state indicator contrast at least 3:1`,ratios.focus>=3&&ratios.held>=3&&ratios.failure>=3)
}
check('custom theme still fits narrow short viewport',await page.locator('[data-runners-panel]').evaluate(e=>e.scrollWidth<=e.clientWidth))
await open('?long=1');check('long physical-node inventory scrolls inside panel',await page.locator('.hermes-runners-scroll').evaluate(e=>e.scrollHeight>e.clientHeight))
await open();await page.locator('[data-slot=runners-chip]').evaluate(el=>{el.style.position='fixed';el.style.left='600px';el.style.right='auto'})
await page.waitForTimeout(100)
check('offscreen status anchor keeps panel reachable',await page.locator('[data-runners-panel]').evaluate(e=>{const r=e.getBoundingClientRect();return r.left>=0&&r.right<=innerWidth}))
check('zero browser page errors',report.errors.length===0)
report.status='passed'
assert.deepEqual(report.failures??=[],[], 'all collected acceptance gates pass')
}catch(error){report.status='failed';report.failure=error.stack;throw error}finally{report.checkCount=report.checks.length;report.captureCount=report.captures.length;await writeFile('report.json',JSON.stringify(report,null,2));await browser.close()}
console.log(JSON.stringify({status:report.status,checks:report.checkCount,captures:report.captures.length,errors:report.errors},null,2))
