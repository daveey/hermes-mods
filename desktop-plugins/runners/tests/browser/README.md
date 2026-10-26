# Chromium regression harness

Run `npm ci` in the parent `tests` directory first, then from this directory run:

    npm ci --ignore-scripts
    npm test

Uses installed `/usr/bin/google-chrome`; override with `CHROME_PATH`. No production requests. Inventory and inspect responses derive from the generated public contract fixtures; names and identifiers are fictional. Enrollment matches the server's version-1 contract.

The harness bundles the actual plugin, React, TanStack Query and Radix. SDK theme/chrome wrappers are approximations, explicitly labeled on every capture: these screenshots are NOT packaged Desktop/live-data acceptance evidence. The native command-palette shell is not mounted; only its registered callback is exercised.

Real Chromium input covers fast type-ahead, middle editing, arrows/Enter, detail Escape, context-menu Escape and scope select. It caught a delayed animation-frame caret reset (`Qartzu` instead of `Quartz`), now applied synchronously in a layout effect before the next key. Geometry assertions cover aligned node health at 390/640/900px in light/dark, plus page overflow. The original wrapping layout failed the narrow health alignment assertion.

Generated reports, HTML/bundle and captures are ignored. Selected sanitized captures live in `../../design/screenshots/`. The long-list fixture verifies internal scrolling and a stationary header within the panel height bound. Installed-shell and live owner data gates remain separate.
