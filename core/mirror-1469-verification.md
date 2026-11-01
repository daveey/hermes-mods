# Mirror 1469 verification

## Frozen scope

Append patches **1428–1469** (42 source commits) to the published 1427-entry series. Source tip: `3241b23316a66eb4d41c26078cd92411f0f295bc`. Upstream exclusion: `016b76da3a8a88e28775e08b6ca2846f471b8eda`. Published patch bytes and ordinals are retained; this is not a renumbering.

Key handoffs: composer editor Escape origin `d9e0c56cb2` is **1431**, documented in [turn-stop-notice.md](turn-stop-notice.md); reviewed-PR historical dependency handling `e5e1b9972b` is **1454**, documented in [mirror-kanban-follow-ups-1428.md](mirror-kanban-follow-ups-1428.md); cron child dependency restoration `3241b23316a` is **1469**, documented in [offline-dns-retry-wait.md](offline-dns-retry-wait.md).

## Executed full-series check

The unchanged verifier exited **0**, using a shared bare source clone:

```sh
python3 -B core/verify_mirror.py \
  --source-repo ../src.git \
  --source-tip 3241b23316a66eb4d41c26078cd92411f0f295bc \
  --base 016b76da3a8a88e28775e08b6ca2846f471b8eda \
  --patch-dir core/patches \
  --policy core/mirror-policy-1469.json \
  --replay-plan core/mirror-replay-1469.json
```

Actual stdout:

```json
{"base": "016b76da3a8a88e28775e08b6ca2846f471b8eda", "bridges": 3, "merge_base": "c419cf7c864ac0ee511dbd9f7091a8bba0b567ee", "patches": 1469, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "3241b23316a66eb4d41c26078cd92411f0f295bc"}
```

This verifies contiguous ordinals, unique SHA headers, exact source-set coverage, complete `git am -3 --empty=keep` replay with explicit bridges, and the complete final tree under the snapshot-bound normalization policy. It is not merely an apply check.

Preflight checked 8,479 unique nonzero historical patch-index object IDs. One obsolete preimage was unavailable; the complete replay nevertheless succeeded by direct hunk application. This is not a claim that every historical index object is present.

## Bridges and normalization review

The existing bridge before 1328 is unchanged. New bridges before **1430** and **1432** represent first-parent merge diffs for `04422ac2f04826f87f48fbced5d4454370faf318` and `fef6b4fb42f81c306725e7799eb4b3b364b0feb3`. Both were compared against `git diff -U1 --binary <merge>^1 <merge>`: the only subsequent change removes a CGNAT literal from an optional hunk-heading suffix. No changed source line was removed. SHA-256 hashes bind the final bridge bytes in the replay plan. The README Applying recipe includes all three boundaries.

The policy retains the preceding substitutions and curated fingerprints. Full residual blob diffs were reviewed before refreshing/adding these exact pairs:

- `hermes_cli/kanban_placement.py` and its test: the normalization differences are exactly the preceding snapshot's existing product/profile substitutions, with new source content shifting the blob IDs. No additional routing normalization was introduced.
- Recorded-route and failed-build tests, remote-notice comments and unloaded-notification comments: anonymized incident/session/card identifiers and one fixture project label; source logic is unchanged.
- Sandbox regression: adjacent Python string literals retain the exact CGNAT address at runtime while avoiding a private-host pattern false positive.

No verifier logic or acceptance condition was edited. New patch files and both new bridge files are scanned against the private denylist; commit hooks also run staged gitleaks, privacy and documentation coverage gates.

## Verification boundary

Feature documents distinguish inspected source-test assertions and implementation-owner test reports from checks actually executed by this mirror. Source suites, live Desktop behavior, real sleep/wake, gateway deployment and physical-event recovery were **not rerun or established here**. In particular, the offline harness repair is not physical-event proof, and the composer implementation owner retains live Desktop verification responsibility.
