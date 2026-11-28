#!/usr/bin/env bash
# Re-run the actual Desktop renderer on every recorded case and compare with
# ../desktop-oracle.json. Needs network for pnpm's first fetch; writes only
# under WORK.
#   HERMES_CORE_GIT=<hermes-agent clone> WORK=<scratch dir> ./replay-oracle.sh
# Requires Node >= 26 (the pinned root's engines) and pnpm 12 on PATH.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
CORE_GIT=${HERMES_CORE_GIT:-$HOME/.hermes/hermes-agent}
COMMIT=$(node -p "require('$HERE/../desktop-oracle.json').core_commit")
WORK=${WORK:?set WORK to a scratch directory}
mkdir -p "$WORK/desk"
git -C "$CORE_GIT" archive "$COMMIT" pnpm-lock.yaml pnpm-workspace.yaml package.json apps/desktop apps/shared | tar -x -C "$WORK/desk"
(cd "$WORK/desk" && pnpm install --frozen-lockfile --ignore-scripts --filter 'hermes...' --store-dir "$WORK/pnpm-store" --config.minimumReleaseAge=0)
node "$HERE/cases-from-fixture.mjs" "$WORK/cases.json"
cp "$HERE/user-steps-oracle.test.tsx" "$WORK/desk/apps/desktop/src/components/assistant-ui/user-steps-oracle.test.tsx"
(cd "$WORK/desk/apps/desktop" && USER_STEPS_ORACLE_IN="$WORK/cases.json" USER_STEPS_ORACLE_OUT="$WORK/oracle-out.json" \
  node_modules/.bin/vitest run --project ui src/components/assistant-ui/user-steps-oracle.test.tsx)
node "$HERE/check-oracle.mjs" "$WORK/oracle-out.json"
