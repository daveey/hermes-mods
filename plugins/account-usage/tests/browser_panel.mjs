import {build} from './harness-deps/node_modules/esbuild/lib/main.js'
import {chromium} from './harness-deps/node_modules/playwright/index.mjs'
import {createServer} from 'node:http'
import {writeFile} from 'node:fs/promises'
import assert from 'node:assert/strict'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
const dir=path.dirname(fileURLToPath(import.meta.url))
const out=await build({entryPoints:[path.join(dir,'harness-entry.js')],bundle:true,write:false,format:'esm',nodePaths:[path.join(dir,'harness-deps/node_modules')],alias:{'@hermes/plugin-sdk':path.join(dir,'harness-sdk.js')}})
const server=createServer((req,res)=>{res.setHeader('Content-Type',req.url==='/app.js'?'text/javascript':'text/html');res.end(req.url==='/app.js'?out.outputFiles[0].text:'<!doctype html><html><head><meta charset="utf-8"><style>body{font:14px system-ui;background:#202124;color:#eee;--border:#555;--accent:#8ac;--ui-accent:#8ac;--ui-text-tertiary:#aaa;padding:30px}section{width:440px;box-sizing:border-box;padding:18px;border:1px solid #555;margin-top:20px}button{color:inherit;background:transparent;cursor:pointer}button:focus-visible{outline:2px solid #8ac}h1{font-size:16px}</style></head><body><h1>SYNTHETIC HARNESS — real plugin, mocked host/API</h1><div id="root"></div><script type="module" src="/app.js"></script></body></html>')})
await new Promise(r=>server.listen(0,'127.0.0.1',r))
const browser=await chromium.launch({headless:true,executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'})
const page=await browser.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message)); const evidence=[]
try {
 await page.goto(`http://127.0.0.1:${server.address().port}`)
 await page.getByRole('button',{name:'Account usage',exact:true}).click()
 const choose=page.getByRole('button',{name:'Try Account B next',exact:true})
 await choose.waitFor({timeout:3000}); await choose.focus(); await page.keyboard.press('Enter')
 await page.getByText('Next to try', {exact:true}).waitFor(); evidence.push('Keyboard account click -> verified pending badge')
 await page.getByRole('button',{name:'Cancel next account',exact:true}).click()
 await page.waitForFunction(()=>!document.body.textContent.includes('Next to try')); evidence.push('Cancel -> verified badge removal')
 await page.getByRole('button',{name:'Try Cooling account next',exact:true}).click()
 await page.getByRole('alert').filter({hasText:'Cooldowns are not reset'}).waitFor(); evidence.push('Cooling row -> readable refusal, no mutation')
 await page.screenshot({path:'/Users/daveey/.hermes/local-fixes/account-usage-selection/ui-harness-refusal.png',fullPage:true})
 await page.getByRole('button',{name:'Try GPT account next',exact:true}).click()
 await page.getByRole('alert').filter({hasText:'provider'}).waitFor(); evidence.push('Wrong provider -> readable refusal')
 await page.evaluate(()=>{window.fixture.refuse=true}); await choose.click()
 await page.getByRole('alert').filter({hasText:'Account became unavailable'}).waitFor(); evidence.push('Backend fresh eligibility refusal displayed')
 await page.evaluate(()=>{window.fixture.refuse=false}); await page.getByRole('button',{name:'Refresh selection',exact:true}).click()
 await page.evaluate(()=>{window.fixture.delay=true;window.fixture.delayMethod='PUT'}); await choose.click()
 await page.waitForFunction(()=>document.body.textContent.includes('Saving selection'))
 await page.evaluate(()=>window.fixture.focus('runtime-b')); await page.evaluate(()=>window.fixture.release())
 await page.waitForFunction(()=>!document.body.textContent.includes('Next to try'))
 await page.getByRole('button',{name:'Refresh selection',exact:true}).click()
 await page.waitForFunction(()=>!document.body.textContent.includes('Loading selection'))
 assert.equal(await page.getByText('Next to try',{exact:true}).count(),0); evidence.push('Delayed PUT completion after tab switch cannot paint old badge')
 await page.evaluate(()=>{window.fixture.focus('runtime-a')}); await page.getByRole('button',{name:'Refresh selection',exact:true}).click()
 await page.getByText('Next to try',{exact:true}).waitFor()
 // A pending cancel cannot race a replacement: both keyboard-accessible controls disable.
 await page.evaluate(()=>{window.fixture.delay=true;window.fixture.delayMethod='DELETE'})
 await page.getByRole('button',{name:'Cancel next account',exact:true}).click()
 assert.equal(await choose.isDisabled(),true)
 assert.equal(await page.getByRole('button',{name:'Cancel next account',exact:true}).isDisabled(),true)
 await page.evaluate(()=>window.fixture.release())
 await page.waitForFunction(()=>!document.body.textContent.includes('Next to try'));evidence.push('Pending cancel disables replace/cancel until exact-target readback')
 // Capture a pending GET in A, switch to B, then release A's stale read.
 await choose.click();await page.getByText('Next to try',{exact:true}).waitFor()
 await page.evaluate(()=>{window.fixture.delay=true;window.fixture.delayMethod='GET'})
 await page.getByRole('button',{name:'Refresh selection',exact:true}).click()
 await page.evaluate(()=>{window.fixture.focus('runtime-b');window.fixture.release()})
 await page.waitForFunction(()=>!document.body.textContent.includes('Loading selection'))
 assert.equal(await page.getByText('Next to try',{exact:true}).count(),0);evidence.push('Delayed GET cannot transfer A pending state onto B')
 await page.evaluate(()=>window.fixture.host.state.focusedSessionOwner.set({connectionId:'remote',profile:'default'}))
 await page.getByRole('alert').filter({hasText:'gateway connection and profile'}).waitFor()
 assert.equal(await page.getByText('Next to try',{exact:true}).count(),0);evidence.push('Foreign connection owner fails closed visibly')
 await page.evaluate(()=>{window.fixture.host.state.focusedSessionOwner.set({connectionId:'local',profile:'default'});window.fixture.focus('runtime-a')})
 await page.getByText('Next to try',{exact:true}).waitFor()
 await page.screenshot({path:'/Users/daveey/.hermes/local-fixes/account-usage-selection/ui-harness.png',fullPage:true})
 const calls=await page.evaluate(()=>window.fixture.calls)
 assert.ok(calls.filter(c=>c.method==='PUT').every(c=>c.body.session_id==='runtime-a'))
 assert.ok(!calls.some(c=>c.method==='PUT'&&c.body.account_id==='inventory-cooling'))
 assert.deepEqual(errors,[])
 console.log(JSON.stringify({synthetic:true,passed:evidence,consoleErrors:errors,calls},null,2))
} finally {await browser.close();server.close()}
