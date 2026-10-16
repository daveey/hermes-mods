import { Button, TRANSCRIPT_DIRECTIVE_AREA } from '@hermes/plugin-sdk';
import { useRef, useState } from 'react';
import { jsx, jsxs } from 'react/jsx-runtime';

// Intentionally identical authority grammar to core's narrow VNC door.
// IPv6 (including bracketed literals) is explicitly unsupported.
export function screenShareUrl(value) {
  if (typeof value !== 'string') return null;
  const match = /^([a-zA-Z0-9.-]+)(?::([0-9]{1,5}))?$/.exec(value);
  if (!match || match[0] !== value) return null;
  const host = match[1];
  const labels = host.split('.');
  if (host.length > 253 || !labels.every(label => /^[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$/.test(label))) return null;
  if (/^[0-9.]+$/.test(host) && !(labels.length === 4 && labels.every(label => /^(0|[1-9][0-9]{0,2})$/.test(label) && Number(label) <= 255))) return null;
  if (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535)) return null;
  return `vnc://${value}`;
}

export function ScreenShare({ attrs, streaming, os }) {
  const [error, setError] = useState('');
  const [pending, setPending] = useState(false);
  const opening = useRef(false);
  const url = screenShareUrl(attrs.host);
  const label = typeof attrs.label === 'string' && attrs.label.trim() ? attrs.label : attrs.host;
  async function open() {
    if (!url || streaming || opening.current) return;
    opening.current = true;
    setPending(true);
    setError('');
    try {
      if (!await os.openExternal(url)) throw new Error('Screen Sharing could not open. Update Hermes Desktop and check that a VNC handler is installed on this computer.');
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      opening.current = false;
      setPending(false);
    }
  }
  return jsxs('div', { children: [
    jsx(Button, { type: 'button', disabled: !url || streaming || pending, onClick: open,
      children: `Screen Share — ${label || 'Invalid host'}` }),
    jsx('div', { style: { color: 'var(--ui-text-secondary)' }, children: attrs.host || 'No host specified' }),
    !url && jsx('div', { role: 'alert', children: 'Invalid Screen Sharing address. Use a DNS name or IPv4 address, optionally with a port; IPv6 is not supported.' }),
    error && jsx('div', { role: 'alert', children: error })
  ] });
}

export default {
  id: 'screen-share',
  name: 'Screen Share',
  register(ctx) {
    ctx.register({ id: 'screen-share-directive', area: TRANSCRIPT_DIRECTIVE_AREA,
      data: { name: 'screenshare', render: props => jsx(ScreenShare, { ...props, os: ctx.os }) } });
  }
};
