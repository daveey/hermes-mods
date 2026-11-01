# Mirror snapshot 1492: executed verification

Frozen source: `6110db6d1b1a2a29d3696894152802b253f60293`. Upstream exclusion and merge-base: `a4994d4ead8f9c915b485cec5689909b349a49df`.

## Full verification

The unchanged verifier ran against a task-owned shared bare clone and exited **0**:

```sh
python3 -B core/verify_mirror.py --source-repo /path/to/shared-src.git \
  --source-tip 6110db6d1b1a2a29d3696894152802b253f60293 \
  --base a4994d4ead8f9c915b485cec5689909b349a49df \
  --patch-dir core/patches --policy core/mirror-policy-1492.json \
  --replay-plan core/mirror-replay-1492.json
```

Exact output:

```json
{"base": "a4994d4ead8f9c915b485cec5689909b349a49df", "bridges": 4, "merge_base": "a4994d4ead8f9c915b485cec5689909b349a49df", "patches": 1492, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "6110db6d1b1a2a29d3696894152802b253f60293"}
```

This checks contiguous ordinals, unique SHA headers, exact non-merge source coverage, complete four-bridge replay and the full normalized final tree. Patches 0001–1484 remain byte-for-byte unchanged. Exactly eight missing commits were appended in reverse non-merge rev-list order.

## Policy and privacy

Policy and replay JSON change only `source_tip` from snapshot 1484. All fingerprints, substitutions, renames, bridge hashes and replay start are unchanged. All 20 unique nonzero index objects in the new patches resolve in the shared source clone. The private denylist scan reports zero matches for each new patch; normal pre-commit gates also apply.

## Feature tests executed

The canonical core test runner executed:

- `tests/hermes_cli/test_kanban_sync_round_connections.py`: **6 passed**, covering shadow/primary replay, success/error closure and retention-floor replacement.
- `tests/hermes_cli/test_kanban_sync_rejections.py`: **20 passed**, including normal sync and CLI dirty-to-clean.
- `tests/e2e/core/platforms/test_discord_rate_limit_contract.py`: its two cases initially failed to import `discord` in the canonical test environment. The actual cold-child contract was then executed for both `channel` and `typing` with `uv run --with discord.py --with aiohttp --with pytest python tests/e2e/core/platforms/test_discord_rate_limit_contract.py <route>`; **both exited 0**, including the cancellation and bounded next-request assertions. This is an isolated dependency-equipped rerun, not a claim that the original runner passed or that a full nightly ran here.

PR #499 exact-head required-check evidence: https://github.com/daveey/hermes/actions/runs/41306900906/job/124853534264 . Broader regression/nightly evidence belongs to the originating PRs; this mirror pass only claims the executions above. Fleet rollout remains staged; no live service or runtime configuration was changed for publication.
