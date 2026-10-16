import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { resolve } from 'node:path';
import { test } from 'node:test';

// Use the actual Desktop React/jsdom versions; no bundled second React.
const require = createRequire(resolve(process.env.HERMES_DESKTOP_DIR || '../hermes-agent/apps/desktop', 'package.json'));
const React = require('react');
const { jsx, jsxs } = require('react/jsx-runtime');
const { JSDOM } = require('jsdom');
const dom = new JSDOM('<div id="root"></div>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const { createRoot } = require('react-dom/client');
const source = readFileSync(new URL('../plugin.js', import.meta.url), 'utf8');
const load = new Function('Button', 'TRANSCRIPT_DIRECTIVE_AREA', 'useRef', 'useState', 'jsx', 'jsxs',
  source.replace(/^import .*;\n/gm, '').replace(/export function /g, 'function ').replace('export default ', 'const plugin = ') + '\nreturn { screenShareUrl, ScreenShare, plugin };');
const { screenShareUrl, plugin } = load(props => jsx('button', props), 'transcript.directive', React.useRef, React.useState, jsx, jsxs);

test('strict authority validation', () => {
  for (const value of ['computer.local', '192.0.2.1', 'computer.local:5901']) assert.equal(screenShareUrl(value), `vnc://${value}`);
  for (const value of [null, '', 'user@host', 'host/', 'host?x', 'host#x', 'host\n', ' host', '[::1]', '::1', 'host:0', 'host:65536', 'host:abc', '-host', 'host..local', '999.1.1.1', '127.1', 'host;open', 'host%20', 'host\\path']) assert.equal(screenShareUrl(value), null, String(value));
});

test('directive renders and opens only on click using client OS; errors and streaming gate', async () => {
  let contribution;
  const calls = [];
  let result = true;
  const os = { openExternal: async url => { calls.push(url); if (result instanceof Error) throw result; return result; } };
  plugin.register({ register: value => { contribution = value; }, os,
    rest: () => { throw new Error('must not use gateway'); } });
  assert.equal(contribution.data.name, 'screenshare');
  const container = document.getElementById('root');
  const root = createRoot(container);
  const render = async (streaming = false, host = 'computer.local:5901') => React.act(async () => {
    root.render(contribution.data.render({ attrs: { host, label: 'Office Mac' }, streaming, connectionId: 'remote-gateway' }));
  });
  await render();
  assert.match(container.textContent, /Screen Share — Office Mac/);
  assert.match(container.textContent, /computer.local:5901/);
  assert.deepEqual(calls, []);
  await render(true);
  assert.equal(container.querySelector('button').disabled, true);
  await React.act(async () => container.querySelector('button').click());
  assert.deepEqual(calls, []);
  await render();
  await React.act(async () => { container.querySelector('button').click(); container.querySelector('button').click(); });
  assert.deepEqual(calls, ['vnc://computer.local:5901']);
  result = false;
  await React.act(async () => container.querySelector('button').click());
  assert.match(container.querySelector('[role=alert]').textContent, /could not open/);
  result = new Error('launcher unavailable');
  await React.act(async () => container.querySelector('button').click());
  assert.equal(container.querySelector('[role=alert]').textContent, 'launcher unavailable');
  await render(false, 'user@host');
  assert.equal(container.querySelector('button').disabled, true);
  const count = calls.length;
  await React.act(async () => container.querySelector('button').click());
  assert.equal(calls.length, count);
  await React.act(async () => root.unmount());
});
