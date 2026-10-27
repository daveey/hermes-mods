#!/usr/bin/env bash
# Exercise plugin source with the core's isolated runtime, without live-home I/O.
set -euo pipefail
core="$(cd "${1:?usage: run-core-integration.sh /path/to/isolated/core/worktree}" && pwd)"
plugins="$(cd "$(dirname "$0")/.." && pwd)"
fixtures="$(mktemp -d "$core/.user-steps-integration.XXXXXX")"
trap 'rm -rf -- "$fixtures"' EXIT
cp -R "$plugins/user-steps" "$fixtures/user-steps"
cp -R "$plugins/ask-gate" "$fixtures/ask-gate"
cd "$core"
scripts/run_tests.sh "$fixtures/user-steps/tests" -p tests.conftest --file-retries 0 --tb=short
