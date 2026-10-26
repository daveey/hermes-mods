import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const [core, home, url, token, control = 'initial'] = process.argv.slice(2);
const require = createRequire(path.join(core, 'apps/desktop/package.json'));
const {_electron, expect} = require('@playwright/test');
const data=path.join(home,'electron-data');
fs.mkdirSync(data,{recursive:true});
const remote={id:'acceptance',kind:'remote',label:'Acceptance',url,authMode:'token',token:{encoding:'plaintext',value:token}};
fs.writeFileSync(path.join(data,'connections.json'),JSON.stringify({version:2,primary:'acceptance',lastUsed:'acceptance',launchMode:'primary',connections:[{id:'local',kind:'local',label:'Local'},remote]}));
fs.writeFileSync(path.join(data,'connection.json'),JSON.stringify({version:1,mode:'remote',remote:{url,authMode:'token',token:remote.token}}));

const app = await _electron.launch({executablePath: require('electron'), args:[path.join(core,'apps/desktop'),'--no-sandbox','--disable-gpu'], env:{...process.env, HERMES_DESKTOP_USER_DATA_DIR:path.join(home,'electron-data'),HERMES_DESKTOP_SKIP_QUIT_CONFIRM:'1',HERMES_DESKTOP_APP_NAME:'GuardAcceptance'},timeout:30000});
const receipt={electron:await app.evaluate(()=>process.versions.electron), control, snapshots:[], sockets:[], wire:[]};
try {
 const page=await app.firstWindow();
 await page.exposeFunction('observeGuardDOM', text=>{if(text.includes('The example app remains HTTP 503 because the prerequisite was deferred.'))receipt.leaked=true;});
 const observeDOM=()=>{new MutationObserver(()=>window.observeGuardDOM(document.body?.innerText || '')).observe(document,{subtree:true,childList:true,characterData:true});};
 await page.addInitScript(observeDOM);
 await page.evaluate(observeDOM);
 page.on('websocket', ws=>{const item={url:ws.url(),closed:false};receipt.sockets.push(item);ws.on('close',()=>item.closed=true);for(const direction of ['framesent','framereceived'])ws.on(direction,frame=>{try{receipt.wire.push({direction,frame:JSON.parse(String(frame.payload))});}catch{}});});
 const composer=page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({visible:true}).first();
 await page.getByRole('button',{name:'Backend ready',exact:true}).waitFor({state:'visible',timeout:90000});
 // Source-mode Desktop boots a local pool before restoring the saved remote route.
 // Backend ready alone can describe that transient local source.
 await expect.poll(()=>{const log=path.join(home,'logs','desktop.log');return fs.existsSync(log)?fs.readFileSync(log,'utf8'):'';},{timeout:90000}).toContain('conn:acceptance::default via=secondary');
 await expect(page.getByRole('button',{name:'Registered gateways: Acceptance',exact:true})).toBeVisible();
 await composer.waitFor({state:'visible',timeout:90000});
 await composer.fill('Repair example app. Why have we not fixed it yet?');
 await composer.press('Enter');
 if(control !== 'initial') {
  await expect.poll(()=>fs.existsSync(path.join(home,'primary-started')),{timeout:90000}).toBe(true);
  if(control === 'stop') {
   receipt.beforeStop=await page.locator('body').innerText();
   await page.locator('[data-slot="composer-root"]').getByRole('button',{name:'Stop',exact:true}).click();
   fs.writeFileSync(path.join(home,'release-primary'),'');
   await expect(page.getByRole('button',{name:'Stop',exact:true})).toHaveCount(0,{timeout:30000});
  } else if(control === 'queue') {
   await composer.fill('Explain HTTP 503. Analysis only; do not change anything.');
   await composer.press('Control+Enter');
   await expect(page.locator('body')).toContainText('1 Queued',{timeout:10000});
   receipt.queued=await page.locator('body').innerText();
   expect(receipt.queued).toContain('1 Queued');
   fs.writeFileSync(path.join(home,'release-primary'),'');
  } else {
   const before=receipt.sockets.length;
   const activeSockets=receipt.sockets.filter(s=>!s.closed);
   if(control === 'reload') {
    receipt.beforeReload=await page.evaluate(()=>({timeOrigin:performance.timeOrigin,href:location.href}));
    await page.reload();
    receipt.afterReload=await page.evaluate(()=>({timeOrigin:performance.timeOrigin,href:location.href}));
    expect(receipt.afterReload.timeOrigin).not.toBe(receipt.beforeReload.timeOrigin);
   } else {
   const cdp=await page.context().newCDPSession(page);
   const proto=await cdp.send('Runtime.evaluate',{expression:'WebSocket.prototype'});
   const objects=await cdp.send('Runtime.queryObjects',{prototypeObjectId:proto.result.objectId});
   const closed=await cdp.send('Runtime.callFunctionOn',{
    objectId:objects.objects.objectId,
    functionDeclaration:`function(origin, all) {
     const gatewaySockets=Array.from(this).filter(s=>new URL(s.url).pathname==='/api/ws');
     const targets=gatewaySockets.filter(s=>s.readyState===1 &&
      (all || (new URL(s.url).origin===origin && new URL(s.url).searchParams.get('profile')==='default')));
     const urls=targets.map(s=>s.url);
     for(const s of targets)s.close(4000,'acceptance reconnect');
     return {urls, remainingOpen:gatewaySockets.filter(s=>s.readyState===1).map(s=>s.url)};
    }`,
    arguments:[{value:url.replace('http:','ws:')},{value:control==='all-sockets'}],returnByValue:true});
   receipt.socketLoss=closed.result.value;
   receipt.closedForReconnect=receipt.socketLoss.urls.length;
   if(control==='all-sockets') {
    expect(receipt.socketLoss.remainingOpen).toEqual([]);
    expect(receipt.socketLoss.urls.some(u=>!new URL(u).searchParams.has('profile'))).toBe(true);
    expect(receipt.socketLoss.urls.some(u=>new URL(u).searchParams.get('profile')==='default')).toBe(true);
   }
   await cdp.detach();
   expect(receipt.closedForReconnect).toBeGreaterThan(control==='all-sockets'?1:0);
   }
   await expect.poll(()=>receipt.sockets.length,{timeout:30000}).toBeGreaterThan(before);
   if(control==='reload' || control==='all-sockets') {
    await expect.poll(()=>activeSockets.every(s=>s.closed),{timeout:30000}).toBe(true);
    receipt.allPriorSocketsClosed=true;
   }
   await page.getByRole('button',{name:'Backend ready',exact:true}).waitFor({state:'visible',timeout:90000});
   if(control==='all-sockets') {
    await expect.poll(()=>receipt.socketLoss.urls.every(u=>receipt.sockets.slice(before).some(s=>s.url===u && !s.closed)),{timeout:30000}).toBe(true);
    receipt.allGatewaySocketsReplaced=true;
   }
   receipt.reconnected=true;
   fs.writeFileSync(path.join(home,'release-primary'),'');
  }
 }
 if(control !== 'stop') {
  await expect(page.locator('body')).toContainText('Unresolved goal',{timeout:90000});
  receipt.snapshots.push(await page.locator('body').innerText());
  await page.screenshot({path:path.join(home,'unresolved.png')});
 }
 if(control !== 'queue') {
  await composer.fill('Explain HTTP 503. Analysis only; do not change anything.');
  await composer.press('Enter');
 }
 await expect(page.locator('body')).toContainText('Analysis only: HTTP 503 means the service is unavailable.',{timeout:90000});
 receipt.snapshots.push(await page.locator('body').innerText());
 await page.screenshot({path:path.join(home,'analysis.png')});
 expect(receipt.leaked).not.toBe(true);
 const submits=receipt.wire.filter(x=>x.direction==='framesent' && x.frame.method==='prompt.submit');
 expect(submits).toHaveLength(2);
 expect(new Set(submits.map(x=>x.frame.params.session_id)).size).toBe(1);
 receipt.sessionId=submits[0].frame.params.session_id;
 if(control==='stop')expect(receipt.wire.some(x=>x.direction==='framesent' && /interrupt|cancel|stop/.test(x.frame.method||''))).toBe(true);
 if(control==='reconnect')expect(receipt.sockets.some(x=>x.closed)).toBe(true);
 const completions=receipt.wire.filter(x=>x.direction==='framereceived' && x.frame.params?.type==='message.complete').map(x=>x.frame.params.payload.status);
 expect(completions).toContain(control==='stop'?'interrupted':'error');
 expect(completions).toContain('complete');
 if(control==='queue')await expect(page.locator('body')).not.toContainText('1 Queued');
 receipt.passed=true;
} catch(error) { receipt.error=String(error); const page=await app.firstWindow(); receipt.failureDOM=await page.locator('body').innerText(); receipt.buttons=await page.getByRole('button').evaluateAll(xs=>xs.map(x=>({text:x.innerText,label:x.getAttribute('aria-label'),title:x.title}))); throw error;
} finally {fs.writeFileSync(path.join(home,'electron-receipt.json'),JSON.stringify(receipt,null,2));await app.close();}
