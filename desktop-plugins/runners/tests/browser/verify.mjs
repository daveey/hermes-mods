import {chromium} from './node_modules/playwright/index.mjs'
import {writeFile} from 'node:fs/promises'
import assert from 'node:assert/strict'
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || '/usr/bin/google-chrome',args:['--no-sandbox']})
const report={errors:[],captures:[],keyboard:[]}
try{
const page=await browser.newPage({viewport:{width:900,height:900}})
page.on('pageerror',e=>report.errors.push(e.message))
await page.goto('file://'+process.cwd()+'/index.html')
await page.locator('[data-slot=runners-chip]').click()
await page.locator('[data-node-row]').first().waitFor()
await page.locator('[data-node-row]').first().focus()
await page.keyboard.type('Quartz')
const input=page.getByRole('textbox',{name:'Search runners'})
assert.equal(await input.inputValue(),'Quartz')
await page.keyboard.press('ArrowLeft');await page.keyboard.press('ArrowLeft');await page.keyboard.type('X')
assert.equal(await input.inputValue(),'QuarXtz')
await page.keyboard.press('Backspace');assert.equal(await input.inputValue(),'Quartz')
report.keyboard.push('type-ahead and middle edit preserve characters/caret')
await page.keyboard.press('Escape');assert.equal(await input.inputValue(),'')
await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
assert.equal(await page.locator('[data-node-row]').first().getAttribute('aria-expanded'),'true')
await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
await page.locator('.hermes-runners-detail').waitFor()
await page.keyboard.press('Escape');assert.equal(await page.locator('.hermes-runners-detail').count(),0)
await page.locator('[data-runner-row]').first().click({button:'right'})
await page.getByRole('menu').waitFor();await page.keyboard.press('Escape')
assert.equal(await page.getByRole('menu').count(),0)
report.keyboard.push('arrows/Enter lazy detail, Escape detail and native context-menu round trip')
await page.evaluate(()=>{const e=[...window.entries.values()].find(e=>e.data.label.startsWith('Show runner:'));if(!e)throw Error('No runner entry');e.data.run()})
await page.locator('.hermes-runners-detail').waitFor()
await page.keyboard.press('Escape')
await page.getByRole('combobox').focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter')
report.keyboard.push('registered palette callback and real select keyboard; native palette shell NOT tested')
for(const width of [900,640,390])for(const theme of ['dark','light']){
await page.setViewportSize({width,height:900})
await page.goto('file://'+process.cwd()+'/index.html?theme='+theme)
await page.locator('[data-slot=runners-chip]').click();await page.locator('[data-node-row]').first().waitFor()
await page.screenshot({path:`${theme}-${width}.png`})
const metrics=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,panel:document.querySelector('[data-runners-panel]').getBoundingClientRect().toJSON(),nodes:[...document.querySelectorAll('[data-node-row]')].map(e=>({text:e.textContent,rect:e.getBoundingClientRect().toJSON()}))}))
assert.ok(metrics.scroll<=width,'no page horizontal overflow')
const alignment=await page.locator('[data-node-row]').evaluateAll(nodes=>nodes.map(n=>{const a=n.firstElementChild.getBoundingClientRect(),b=n.lastElementChild.getBoundingClientRect();return Math.abs(a.top-b.top)<3}))
assert.ok(alignment.every(Boolean),'node health stays aligned at right, including narrow fallback')
report.captures.push({theme,width,metrics})
}
await page.goto('file://'+process.cwd()+'/index.html?long=1')
await page.locator('[data-slot=runners-chip]').click();await page.locator('[data-node-row]').first().waitFor()
const long=await page.evaluate(()=>{
 const sc=document.querySelector('.hermes-runners-scroll'),panel=document.querySelector('[data-runners-panel]'),head=document.querySelector('.hermes-runners-head');
 const top=head.getBoundingClientRect().top;sc.scrollTop=1000;
 return {scrolls:sc.scrollHeight>sc.clientHeight,headerStable:head.getBoundingClientRect().top===top,height:panel.getBoundingClientRect().height,max:innerHeight*.7}
})
assert.ok(long.scrolls && long.headerStable && long.height<=long.max,'long inventory scrolls inside bounded panel without moving header')
report.longList=long
// A crowded native status bar can put the trigger beyond a narrow viewport.
// The panel must stay reachable rather than remain tethered to that trigger.
await page.setViewportSize({width:390,height:900})
await page.goto('file://'+process.cwd()+'/index.html')
await page.locator('[data-slot=runners-chip]').evaluate(el=>{
  el.style.position='fixed';el.style.left='600px';el.style.right='auto';el.click()
})
await page.locator('[data-node-row]').first().waitFor()
const detached=await page.locator('[data-runners-panel]').evaluate(el=>el.getBoundingClientRect().toJSON())
report.offscreenAnchor=detached
assert.ok(detached.left>=0 && detached.right<=390,'panel stays inside viewport even when status trigger is offscreen')
assert.deepEqual(report.errors,[])
}finally{await writeFile('report.json',JSON.stringify(report,null,2));await browser.close()}
console.log(JSON.stringify(report,null,2))
