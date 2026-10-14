#!/usr/bin/env bash
# Desktop-half flow test: runs coach's plugin.js (plus the real spin-out plugin.js it hands over to) in Node against a
# mocked @hermes/plugin-sdk. No app, no model calls. Needs a Hermes checkout with node_modules (react, react-dom):
# HERMES_CHECKOUT=~/code/hermes-agent by default.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MODS="$HERE/../../../.."
AGENT="${HERMES_CHECKOUT:-$HOME/code/hermes-agent}"
WORK="$AGENT/.plugin-probe-coach"
rm -rf "$WORK" && mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
for p in coach spin-out; do
  sed "s#from '@hermes/plugin-sdk'#from './sdk-mock.mjs'#" "$MODS/desktop-plugins/$p/plugin.js" > "$WORK/$p.mjs"
done
cp "$HERE/run.mjs" "$HERE/sdk-mock.mjs" "$WORK/"
cd "$WORK" && NODE_PATH="$AGENT/node_modules" node run.mjs
