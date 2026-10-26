# Inline Screen Share / strict client VNC launch

## Behavior

The screen-share disk plugin renders `::screenshare{host="computer.example" label="Office Mac"}` as a native button with the target address. Clicking opens the local Desktop OS VNC handler (Screen Sharing on macOS), even for a chat on a remote gateway. Rendering and streaming never launch anything. Errors appear inline.

## How it works

Core patch **0109** adds a separate narrow authority-only VNC branch before URL trimming/parsing in `openExternalUrl`. It accepts ASCII DNS labels or canonical IPv4 plus an optional 1–65535 port. It rejects credentials, path/query/fragment, controls, shell syntax, malformed numeric IPs and IPv6. The original string is passed directly to the injected client `openExternal`, never the WSL command-shell launcher. All other protocol handling remains unchanged.

The disk plugin independently validates the same grammar, uses SDK `Button` and React, disables streaming/invalid/pending buttons, and invokes only `ctx.os.openExternal` in its click handler. A ref prevents duplicate launches before React processes pending state.

## Code

- `apps/desktop/electron/external-open.ts`
- `apps/desktop/electron/external-open.test.ts`
- [desktop-plugins/screen-share](../desktop-plugins/screen-share/)

## Tests

From Desktop: `npx vitest run electron/external-open.test.ts` (15 passing), `npx tsc --noEmit`, and `npx eslint electron/external-open.ts electron/external-open.test.ts`.

Plugin React/jsdom tests and their dependency setup are in its README. They prove UI/click behavior with an injected OS boundary; they are not proof that a live packaged Desktop launched Screen Sharing.

## Applying

Apply patch 0109 atop the preceding fork patch series with `git am -3 --empty=keep`, rebuild Desktop, and install the screen-share disk plugin. The plugin can hot-load, but the main-process core capability requires an updated Desktop build. Do not restart an active user's app to activate it without permission.

## Gotchas

IPv6 is deliberately unsupported. A reachable address must be discovered from the client network, not invented or assumed from a remote gateway's SSH config. VNC handler launch is not evidence of authentication or connection success. On a non-Mac client, a VNC URL handler must be installed. Missing/old core capability is an explicit inline failure, never a remote backend fallback.

## Why not only a plugin?

The UI is entirely a disk plugin. Core previously refused every `vnc:` URL at the single external-open allowlist; a plugin cannot safely bypass that OS security boundary. The minimal core change adds only validated VNC authorities, not arbitrary URL schemes.
