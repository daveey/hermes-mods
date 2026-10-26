import { createRequire } from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const [core, home, url, token] = process.argv.slice(2);
const require = createRequire(path.join(core, 'apps/desktop/package.json'));
const {_electron, expect} = require('@playwright/test');
const data=path.join(home,'electron-data');
fs.mkdirSync(data,{recursive:true});
const remote={id:'acceptance',kind:'remote',label:'Acceptance',url,authMode:'token',token:{encoding:'plaintext',value:token}};
fs.writeFileSync(path.join(data,'connections.json'),JSON.stringify({version:2,primary:'acceptance',lastUsed:'acceptance',launchMode:'primary',connections:[{id:'local',kind:'local',label:'Local'},remote]}));
fs.writeFileSync(path.join(data,'connection.json'),JSON.stringify({version:1,mode:'remote',remote:{url,authMode:'token',token:remote.token}}));

const app = await _electron.launch({executablePath: require('electron'), args:[path.join(core,'apps/desktop'),'--no-sandbox','--disable-gpu'], env:{...process.env, HERMES_DESKTOP_USER_DATA_DIR:path.join(home,'electron-data'),HERMES_DESKTOP_SKIP_QUIT_CONFIRM:'1',HERMES_DESKTOP_APP_NAME:'GuardAcceptance'},timeout:30000});
const receipt={electron:await app.evaluate(()=>process.versions.electron), snapshots:[]};
try {
 const page=await app.firstWindow();
 await page.exposeFunction('observeGuardDOM', text=>{if(text.includes('The example app remains HTTP 503 because the prerequisite was deferred.'))receipt.leaked=true;});
 await page.evaluate(()=>{new MutationObserver(()=>window.observeGuardDOM(document.body.innerText)).observe(document.body,{subtree:true,childList:true,characterData:true});});
 const composer=page.locator('[data-slot="composer-root"] [contenteditable="true"]').filter({visible:true}).first();
 await page.getByRole('button',{name:'Backend ready',exact:true}).waitFor({state:'visible',timeout:90000});
 await composer.waitFor({state:'visible',timeout:90000});
 await composer.fill('Repair example app. Why have we not fixed it yet?');
 await composer.press('Enter');
 await expect(page.locator('body')).toContainText('Unresolved goal',{timeout:90000});
 receipt.snapshots.push(await page.locator('body').innerText());
 await page.screenshot({path:path.join(home,'unresolved.png')});
 await composer.fill('Explain HTTP 503. Analysis only; do not change anything.');
 await composer.press('Enter');
 await expect(page.locator('body')).toContainText('Analysis only: HTTP 503 means the service is unavailable.',{timeout:90000});
 receipt.snapshots.push(await page.locator('body').innerText());
 await page.screenshot({path:path.join(home,'analysis.png')});
 expect(receipt.leaked).not.toBe(true);
 receipt.passed=true;
} finally {fs.writeFileSync(path.join(home,'electron-receipt.json'),JSON.stringify(receipt,null,2));await app.close();}
