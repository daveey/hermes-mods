#!/usr/bin/env bash
# Desktop-half flow test: runs plugin.js in Node against a mocked @hermes/plugin-sdk (no app, no model calls).
# Needs a Hermes checkout with node_modules (react, react-dom): HERMES_CHECKOUT=~/code/hermes-agent by default.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
AGENT="${HERMES_CHECKOUT:-$HOME/code/hermes-agent}"
WORK="$AGENT/.plugin-probe-spin-out"
rm -rf "$WORK" && mkdir -p "$WORK"
trap 'rm -rf "$WORK"' EXIT
sed "s#from '@hermes/plugin-sdk'#from './sdk-mock.mjs'#" "$HERE/../../../../desktop-plugins/spin-out/plugin.js" > "$WORK/plugin.mjs"
cp "$HERE/run.mjs" "$HERE/sdk-mock.mjs" "$WORK/"
cd "$WORK" && NODE_PATH="$AGENT/node_modules" node run.mjs
