#!/usr/bin/env bash
# Exercise plugin source with the core's isolated runtime, without live-home I/O.
# Usage: run-core-integration.sh /path/to/isolated/core/worktree [extra run_tests.sh / pytest args]
set -euo pipefail
core="$(cd "${1:?usage: run-core-integration.sh /path/to/isolated/core/worktree [args]}" && pwd -P)"
shift
# Never the live install: fixtures would be copied into it and run_tests.sh writes there.
for home in "$HOME/.hermes" "${HERMES_HOME:-}"; do
  live="$( [ -n "$home" ] && cd "$home/hermes-agent" 2>/dev/null && pwd -P || true)"
  if [ -n "$live" ] && [ "$core" = "$live" ]; then
    echo "refusing: $core is the live install; pass an isolated core worktree" \
         "(git -C $core worktree add --detach <dir> HEAD)" >&2
    exit 2
  fi
done
plugins="$(cd "$(dirname "$0")/.." && pwd)"
fixtures="$(mktemp -d "$core/.user-steps-integration.XXXXXX")"
trap 'rm -rf -- "$fixtures"' EXIT
# Preserve the repository layout for cross-producer and Desktop contract tests.
mkdir -p "$fixtures/plugins/coach" "$fixtures/desktop-plugins"
for plugin in user-steps ask-gate session-outcome open-asks; do
  cp -R "$plugins/$plugin" "$fixtures/plugins/$plugin"
done
cp "$plugins/coach/placement_evidence.py" "$fixtures/plugins/coach/placement_evidence.py"
cp -R "$plugins/../desktop-plugins/user-steps" "$fixtures/desktop-plugins/user-steps"
cd "$core"
scripts/run_tests.sh "$fixtures/plugins/user-steps/tests" -p tests.conftest --file-retries 0 --tb=short "$@"
