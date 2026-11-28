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
fixtures="$(mktemp -d "$core/tests/hermes_cli/.user-steps-integration.XXXXXX")"
trap 'rm -rf -- "$fixtures"' EXIT
if [[ -n "${HERMES_HUB_CLIENT_REAL:-}" ]]; then
  cp "$HERMES_HUB_CLIENT_REAL" "$fixtures/courier.py"
fi
# Preserve the repository layout for cross-producer and Desktop contract tests.
mkdir -p "$fixtures/plugins/coach" "$fixtures/desktop-plugins"
for plugin in user-steps ask-gate session-outcome open-asks hub-sync session-panel; do
  cp -R "$plugins/$plugin" "$fixtures/plugins/$plugin"
done
cp "$plugins/kanban_inventory.py" "$fixtures/plugins/kanban_inventory.py"
cp "$plugins/coach/placement_evidence.py" "$fixtures/plugins/coach/placement_evidence.py"
cp -R "$plugins/../desktop-plugins/user-steps" "$fixtures/desktop-plugins/user-steps"
cd "$core"
if [[ "${1:-}" == --remote-card-composed ]]; then
  shift
  reports="${1:?pass an absolute report directory}"
  shift
  [[ "$reports" = /* ]] || { echo 'report directory must be absolute' >&2; exit 2; }
  test -f "$fixtures/courier.py"
  test -f tests/conftest.py
  test -f tests/hermes_cli/test_card_answer_courier_integration.py
  mkdir -p "$reports"
  for stem in test_remote_card_async_custody test_remote_card_courier test_remote_card_review_race \
              test_kanban_mirror_release_core test_local_authority_core test_local_authority_core_refusals \
              test_local_authority_core_binding; do
    rm -f "$reports/$stem.xml"
    scripts/run_tests.sh "$fixtures/plugins/user-steps/tests/$stem.py" \
      --file-retries 0 --tb=short -v -rA --junitxml="$reports/$stem.xml" "$@"
  done
  "${HERMES_PYTHON:-$core/.venv/bin/python}" "$plugins/user-steps/composed_results.py" "$reports"
else
  scripts/run_tests.sh "$fixtures/plugins/user-steps/tests" --file-retries 0 --tb=short "$@"
fi
