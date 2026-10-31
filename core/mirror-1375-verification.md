# Mirror 1375 verification

## Frozen inventory

Source tip: `93843e3a493cdd647b59f65d6a29967801feb621`.
Upstream exclusion: `6bb377a51d5c8e279a7a5e34d717d653d421d064`.
Previous public acceptance: `f748bf57152569b2fdeb9ac3bb5c6bf7e6331528`.

Both source remotes and the public remote were fetched before computing unpublished SHA headers, then fetched again immediately before generating patches. This pass appends seven previously absent headers in `rev-list --reverse --no-merges` order as 1369–1375. Every patch has a README row and feature document. SHA-256 comparison confirms all 1368 pre-existing patch files remain byte-identical. No old range is duplicated or renumbered.

## Executed acceptance

On the Linux hub, using a detached public worktree and a shared bare clone of the source object store:

- `python3 -B core/check_coverage.py`: exit 0.
- `python3 -B -m unittest discover -s core -p test_verify_mirror.py`: 25 tests passed.
- Private denylist scan: each of seven new patch files and the inherited bridge returned 0 matches.
- Non-patch staged whitespace check: exit 0. Mail-patch context blank lines and standard format-patch trailers intentionally retain their meaningful whitespace.
- Full unchanged verifier command below: exit 0.

```sh
python3 -B core/verify_mirror.py \
  --source-repo ../source.git \
  --source-tip 93843e3a493cdd647b59f65d6a29967801feb621 \
  --base 6bb377a51d5c8e279a7a5e34d717d653d421d064 \
  --patch-dir core/patches \
  --policy core/mirror-policy-1375.json \
  --replay-plan core/mirror-replay-1375.json
```

Exact stdout:

```json
{"base": "6bb377a51d5c8e279a7a5e34d717d653d421d064", "bridges": 1, "merge_base": "ef35e8b9e1a8bc7f0efb50e4f2d68f1ec052f6a8", "patches": 1375, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "93843e3a493cdd647b59f65d6a29967801feb621"}
```

## Policy adaptation and failure analysis

The explicit replay plan retains the historical start, bridge bytes, boundary, source merge and SHA-256; only its snapshot tip changes. The policy retains all existing substitutions, renames and fingerprints except the updated placement-test blob pair:

- Source: `062efee7c8cd1de3027415a456efb368cb370ff3`.
- Applied: `32b31427a01325705f9cc2bab905e05f4c2b28ee`.

Why the first full check failed: patch 1373 appends regression tests to a fixture that already has reviewed public normalization, so its source/applied blob IDs changed. Why the prior policy was stale: it was copied from snapshot 1368, whose pair pins the old file. Why preliminary checks did not reject it: coverage and unit tests do not compare this snapshot's complete tree; the full acceptance gate did reject it as intended. Prevention here is to review the exact inherited normalization before repinning, never accept arbitrary divergence.

Byte-prefix assertions verified that the new source consists of its old source plus a suffix, and the new public blob consists of its old public blob plus the **identical suffix**. The only differences are therefore the previously reviewed fixture normalization; no test delta was discarded. The new pinned pair was installed only after this proof, and the unchanged full verifier then passed. No verification logic or acceptance checks were weakened.

Index-object preflight checked 8116 distinct nonzero IDs. One historical preimage, `9ff9444f10`, is unavailable in the shared object store; full replay nonetheless completed without requiring that fallback object. No patch bytes were regenerated to hide it.

## Limits

This acceptance proves source-header completeness/uniqueness, published ordinal continuity, full-series replay with the explicit bridge, and normalized full-tree equality at the frozen tip. Source runtime regression suites are inventoried in the seven feature docs but were not rerun here. No live runtime activation or host resource-policy certification is claimed.
