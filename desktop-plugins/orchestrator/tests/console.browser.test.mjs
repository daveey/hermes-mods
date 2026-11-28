import test from "node:test";
import assert from "node:assert/strict";
import { build } from "esbuild";
import { chromium } from "playwright";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
const here = dirname(fileURLToPath(import.meta.url));
test("Chromium: geometry, sequential CDP keys, server counts, routed actions and readbacks", async () => {
  const dir = mkdtempSync(join(tmpdir(), "orchestrator-browser-")),
    errors = [];
  const bundle = await build({
    stdin: {
      resolveDir: here,
      contents: `
    import React from 'react'; import {createRoot} from 'react-dom/client';
    import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
    import {FlightDeck,openRow,decide,steer,applyFrame,createSource} from '../plugin.js';
    const route={connectionId:'remote-1',mode:'remote',profile:'desktop-work',targetProfile:'work'};
    window.calls=[]; window.ownerRoute=route;
    window.host={profileRoutes:async()=>[route],connections:async()=>[{id:'remote-1',label:'Remote One',gatewayId:'remote-one'}],openSession:(...args)=>window.calls.push(['open',...args]),navigate(){},activeConnectionId:()=>null,state:{profile:{get:()=>''}}};
    const request={requestId:'ask',revision:2,state:'open',owner:{gatewayId:'remote-one',profile:'work',storedSessionId:'chat'},scope:{complete:true,markdown:'Approve deployment?'},choices:[{id:'yes',effect:'approve',label:'Approve'}]};
    const row={key:'ask',title:'Approve deployment',state:'human',session_id:'chat',route:{gatewayId:'remote-one',targetProfile:'work'},request};
    const parent={key:'build',title:'Shared build',state:'running',session_id:'build-chat',route};
    const fixture={schema:'OrchestratorSnapshot',version:1,cursor:'initial',human:[row],machinery:[{key:'gate',title:'Admission backlog',label:'Waiting for admission to lane',state:'waiting',route}],sessions:[],chains:[{key:'build',prerequisite:parent,waiters:[{key:'one',title:'First waiter',state:'waiting',route},{key:'two',title:'Second waiter',state:'waiting',route}]}],totals:{human:91,machinery:72,sessions:53,running:34,filtered:{human:91,machinery:72,sessions:53,running:34}},lanes:[{key:'work',label:'Work',running:9,over:2,share:3,actual_share:.6,target_share:.4,blocked_on_you:4,governor_held:5,ready:7,gate:{admitted:12,budget:50},cost:{window:12.34},cap:7}],sources:[],telemetry:{}};
    let current=structuredClone(fixture);window.fixture=fixture;
    const ctx={storage:{get:async(k,d)=>JSON.parse(localStorage.getItem(k)||'null')||d,set:async(k,v)=>{window.calls.push(['persist',k,v]);localStorage.setItem(k,JSON.stringify(v))}}};
    window.ctx=ctx;window.row=row;window.functions={openRow,decide,steer,applyFrame,createSource};
    const source={route:()=>route,stream:()=>()=>{},read:async q=>{const s=structuredClone(current);if(q){s.human=s.human.filter(r=>r.title.toLowerCase().includes(q.toLowerCase()));s.machinery=[];s.chains=[];s.totals.filtered={human:s.human.length,machinery:0,sessions:0,running:0}}return s}};
    let root=createRoot(document.getElementById('root'));const qc=new QueryClient({defaultOptions:{queries:{retry:false}}});
    window.render=()=>root.render(React.createElement(QueryClientProvider,{client:qc},React.createElement(FlightDeck,{ctx,source})));
    window.refresh=async()=>{current={...current,cursor:'refresh'};await qc.invalidateQueries()};
    window.change=async()=>{current={...current,cursor:'missing',sources:[{name:'board:secondary',state:'unknown'}],totals:{...current.totals,human:null,filtered:{...current.totals.filtered,human:null}}};await qc.invalidateQueries()};
    window.hermesDesktop={api:async req=>{window.calls.push(['api',req]);if(req.path.includes('/steering'))return {steering:true};if(req.path.includes('/lanes?'))return {lanes:{work:{share:4,limits:{max_workers:8}}}};if(req.path.includes('/decisions'))return {operation:{operationId:'op'}};if(req.path.includes('/operations/'))return {operation:{operationId:'op',dispatchState:'pending'},request:{...request,resolution:{operationId:'op'}}};if(req.path.includes('/operations?'))return {operation:null};return {}}};
    window.render();
  `,
    },
    bundle: true,
    nodePaths: [join(here, "node_modules")],
    write: false,
    format: "iife",
    plugins: [
      {
        name: "sdk",
        setup(b) {
          b.onResolve({ filter: /^@hermes\/plugin-sdk$/ }, () => ({
            path: "sdk",
            namespace: "mock",
          }));
          b.onLoad({ filter: /.*/, namespace: "mock" }, () => ({
            contents: `export {useQuery} from '@tanstack/react-query';export const host=new Proxy({}, {get:(_,k)=>(...args)=>window.host[k](...args)});export const ROUTES_AREA='routes',SIDEBAR_NAV_AREA='nav',PALETTE_AREA='palette';`,
            resolveDir: here,
          }));
        },
      },
    ],
  });
  writeFileSync(
    join(dir, "index.html"),
    `<style>body{margin:0;background:#080b10;--ui-bg-primary:#080b10;--ui-bg-secondary:#10151d;--ui-text-primary:#e6edf3;--ui-text-tertiary:#9aa6b8;--ui-stroke-secondary:#283342;--ui-accent:#388bfd;--ui-success:#3fb950;--ui-warning:#e3b341;--ui-danger:#f85149;--ui-bg-overlay:#080b10dd}#root{height:100vh}</style><div id="root"></div><script>${bundle.outputFiles[0].text}</script>`,
  );
  const browser = await chromium.launch({ headless: true });
  try {
    const page = await browser.newPage();
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => {
      if (m.type() === "error") errors.push(m.text());
    });
    await page.goto("file://" + join(dir, "index.html"));
    await page.locator("[data-row=ask]").waitFor();
    await page.locator("[data-row=ask]").focus();
    await page.keyboard.press("ArrowDown");
    await page.waitForFunction(() =>
      document.querySelector("[data-row=gate]").classList.contains("cursor"),
    );
    assert.equal(
      await page.evaluate(() =>
        document.activeElement?.getAttribute("data-row"),
      ),
      "ask",
    );
    await page.evaluate(() => window.refresh());
    await page.waitForFunction(() =>
      document.querySelector("[data-row=gate]").classList.contains("cursor"),
    );
    assert.equal(
      await page.evaluate(() =>
        document.activeElement?.getAttribute("data-row"),
      ),
      "ask",
    );
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("Enter");
    await page.getByRole("button", { name: "Back", exact: true }).click();
    for (const [width, height] of [
      [1440, 900],
      [900, 760],
      [640, 760],
    ]) {
      await page.setViewportSize({ width, height });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      const overlaps = await page.evaluate(() =>
        [...document.querySelectorAll(".oc-lane")].flatMap((row) =>
          [...row.children]
            .slice(1)
            .filter((cell, i) => {
              const a = row.children[i].getBoundingClientRect(),
                b = cell.getBoundingClientRect();
              return Math.abs(a.top - b.top) < 2 && a.right > b.left + 0.5;
            })
            .map((c) => c.textContent),
        ),
      );
      assert.deepEqual(overlaps, [], String(width));
      await page.locator(".oc").focus();
      const cdp = await page.context().newCDPSession(page);
      await cdp.send("Input.dispatchKeyEvent", {
        type: "keyDown",
        key: "/",
        code: "Slash",
      });
      await cdp.send("Input.dispatchKeyEvent", {
        type: "keyUp",
        key: "/",
        code: "Slash",
      });
      await page.keyboard.type("Approve");
      await page.waitForFunction(() =>
        document.querySelector("#fcount").textContent.includes("On you 1"),
      );
      assert.equal(await page.locator("mark").count(), 1);
      await page.keyboard.press("Enter");
      await page.getByRole("button", { name: "Back", exact: true }).waitFor();
      assert.equal(
        await page
          .locator(".oc-inspector")
          .evaluate((el) => el.getBoundingClientRect().width),
        380,
      );
      await page.getByRole("button", { name: "Back", exact: true }).click();
      assert.equal(await page.locator(".oc-inspector").count(), 0);
      await page.keyboard.press("Meta+k");
      await page.getByRole("dialog", { name: "Command palette" }).waitFor();
      await page.keyboard.press("Escape");
      assert.equal(await page.getByRole("dialog").count(), 0);
      await page.locator(".oc").focus();
      await page.keyboard.press("?");
      await page.getByRole("dialog", { name: "Keyboard shortcuts" }).waitFor();
      await page.keyboard.press("Tab");
      await page.keyboard.press("Escape");
      assert.equal(
        await page
          .locator(".oc")
          .evaluate((el) => el.contains(document.activeElement)),
        true,
      );
      await page.keyboard.press("Escape");
      await page.waitForFunction(
        () => document.querySelector("#fcount").textContent === "/",
      );
      await page.locator("[data-row=build]").waitFor();
      await cdp.detach();
    }
    assert.match(
      await page.locator(".oc-totals").textContent(),
      /On you 91.*Machinery 72.*Running 34/,
    );
    assert.equal(await page.locator("[data-row=build]").count(), 1);
    assert.equal(await page.locator(".oc-chain .nested").count(), 2);
    assert.match(
      await page.locator("[data-row=build]").getAttribute("class"),
      /running/,
    );
    assert.match(
      await page.locator("[data-row=gate]").textContent(),
      /Waiting for admission to lane/,
    );
    await page.evaluate(async () => {
      await window.functions.openRow(window.row);
      await window.functions.decide(window.ctx, window.row, { id: "yes" });
      await window.functions.steer(window.ownerRoute, "work", "share", 4);
    });
    const calls = await page.evaluate(() => window.calls);
    assert.deepEqual(
      calls.find((c) => c[0] === "open"),
      [
        "open",
        "chat",
        {
          route: {
            connectionId: "remote-1",
            mode: "remote",
            profile: "desktop-work",
            targetProfile: "work",
          },
        },
      ],
    );
    const post = calls.find(
      (c) => c[0] === "api" && c[1].path.includes("/decisions"),
    );
    assert.ok(calls.findIndex((c) => c[0] === "persist") < calls.indexOf(post));
    assert.equal(post[1].profile, "work");
    assert.equal(post[1].connectionId, "remote-1");
    const before = calls.filter((c) => c[0] === "persist").length;
    await page.evaluate(() =>
      window.functions.decide(window.ctx, window.row, { id: "yes" }),
    );
    assert.equal(
      await page.evaluate(
        () => window.calls.filter((c) => c[0] === "persist").length,
      ),
      before,
    );
    await page.evaluate(() => window.change());
    await page.waitForFunction(() =>
      document
        .querySelector(".oc-totals")
        .textContent.includes("On you unknown"),
    );
    assert.match(
      await page.locator("footer").textContent(),
      /board:secondary: unknown/,
    );
    assert.deepEqual(errors, []);
    await page.screenshot({
      path: join(dir, "flight-deck.png"),
      fullPage: true,
    });
    console.log(
      "Chromium passed: 1440×900, 900×760, 640×760; CDP keyboard sequence; routed decision+steering readback; server totals; unknown source",
    );
  } finally {
    await browser.close();
    rmSync(dir, { recursive: true, force: true });
  }
});
