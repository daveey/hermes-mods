# Screen Share

An inline native Hermes button that opens Screen Sharing **on the computer running Desktop**, even when the chat belongs to a remote gateway. Nothing opens until clicked. Streaming buttons are disabled.

Emit this directive on its own line (not inside a code fence):

```text
::screenshare{host="computer.example" label="Office Mac"}
```

Discover the real reachable address before emitting it; never invent an address. The button displays both label and address. DNS names and canonical IPv4 addresses with an optional port (1–65535) are accepted. Credentials, paths, query strings, fragments, controls, shell characters and IPv6 are rejected. IPv6 is intentionally unsupported.

## Installation and requirements

Link this folder into `$HERMES_HOME/desktop-plugins/screen-share` or use the repository installer. Desktop disk plugins hot-reload. Requires the [strict VNC core capability](../../core/screen-share.md); older builds render the button but report that opening failed. No backend plugin, shell, SSH command or gateway launch is used: the only action is `ctx.os.openExternal('vnc://…')` from the click handler.

macOS registers Screen Sharing as the VNC handler. Other client operating systems need an installed VNC URL handler; otherwise the button reports failure. For non-Mac target machines, explicitly explain the appropriate remote-desktop fallback rather than promising macOS Screen Sharing. Opening a handler does not prove successful authentication or connection. OS/SDK errors are displayed inline.

## Agent guidance

When asking the user to act on another Mac, include the directive next to the instructions. Keep terminal commands on the user-terminal tool; this button is for GUI access, not command execution. The local Desktop must be able to reach the address (an SSH alias on a gateway is not sufficient).

## Tests

From the hermes-mods root, with Node 22+ and a sibling Hermes checkout with installed Desktop dependencies:

```sh
node --test --test-reporter=tap desktop-plugins/screen-share/test/plugin.test.mjs
```

Set `HERMES_DESKTOP_DIR` if that checkout is elsewhere. Tests use the Desktop's React and jsdom, mock only the SDK Button and OS boundary, and verify label/address rendering, no mount-time launch, disabled streaming/invalid states, one launch per click while pending, client-only routing despite remote-gateway context, strict hostile-host rejection, and false/rejected launcher errors. Core's `electron/external-open.test.ts` separately tests the OS dispatch and validation.
