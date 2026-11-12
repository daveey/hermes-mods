# Mirror snapshot 2409: inventory, integration and repaired tree

## Scope

Append **2254–2409** (156 non-merge source commits) to the immutable 2253 series.
Frozen source: `10a929b4a93d10921b7a16a40bd699e5507ffd8e`.
Frozen upstream exclusion: `e3bf4be5df04cf72dab4914b073361c71af1dbe1`.
Merge-base: `78abaafcccec20a546c94e8734a70e38c08a6a69`.
Published ordinals, old mail bytes, the 41 old bridges, all historical policies,
and `verify_mirror.py` are unchanged. The replay starts at the historical pinned
start, not the new merge-base.

The requested creation-policy snapshot is entry **2276** (`1d4a810f623fbee38ce57709a69f9d26edb4c9e2`),
its pool-independent audit follow-up is **2287** (`88e7bdfbfcead592f453ca953c81a8096f48aa68`),
and bounded missing-pickup evidence/group refresh are **2322–2323**
(`6f5fad1053bf02479e022cec06cb384ce141b562`, `16e2f7f9b5a15445de9e8bf751d207a1ae0250b9`).
All 156 entries are mapped to 45 feature documents in the README.

## Three integration bridges

- Before 2254: source merge `7fcb3351f0fb5a9a377f861fd82b7e9862c3b00d` first-parent delta.
- Before 2255: source merge `caa185a23c0e2c333361a77cd18b8255d90cb18c` delta applied to the current replay state.
  This older branch does not descend from the previous snapshot. The two conflict resolutions preserve
  newer fork behavior: `gateway_runtime.py` keeps `_recover_start_refusal` and the source `detail` propagation;
  `process_registry.py` retains both `ProcessEnvScopeMixin` and `ProcessForegroundHoldMixin`.
  The bridge is generated against that replay preimage, not by overwriting the tree with the old branch.
- Before 2256: source merge `9f84f76fc8c2c6d42e90c8eb92dcfd77abe28168` first-parent delta.

The plan now has **44 hash-pinned bridges** and the unchanged three ancestry-only entries.
Final whole-tree comparison verifies the later integration resolution and newer mainline work are retained.

## Normalization and review

The seven inherited fingerprint pairs changed only because their source files changed.
For each, the added/removed lines of its normalized residual diff equal the previously reviewed
2253 pair; no new functional divergence was admitted. Two new exact fingerprints cover:

- `plugin-catalog/coldstart.yaml`: a public CGNAT range in descriptive prose is expressed as
  “Tailscale CGNAT (RFC 6598)”.
- `tests/hermes_cli/test_gateway_migrate_backends.py`: the synthetic non-loopback detached-process
  host becomes the documentation address `192.0.2.2`; the assertion classifies process ancestry,
  not the address range. This feature suite was not rerun by the mirror.

The generic hostname rule now excludes a preceding `capability:` protocol prefix. Entries
2371–2374 retain the operational `capability:ssh-mbp` label verbatim in code, tests and prose;
this is a protocol identifier, not a private machine name. An explicit normalization assertion passes.
Other inherited substitutions and contributor rename rules are retained.
All mail authors use the public no-reply attribution. Entry 2277's private co-author host is
replaced by an example address; entry 2291's description-only `From ` line is mbox-escaped.
Upstream third-party contributor attribution is retained, not mistaken for private deployment identity.

Independent review compared all 156 mails with source, reviewed bridge construction and policy
changes, and independently ran the unchanged verifier successfully. Documentation review corrected
five stale/overbroad statements; all documented test references were checked against frozen source.
The final protocol-literal preservation correction was followed by a new complete CLI and README replay.

## Executed checks

- Full unchanged CLI verifier: exit **0**, exact output below.
- Actual README Bash recipe: exit **0**, including a separate complete replay and mandatory
  additive hostname repair followed by unchanged whole-tree comparison and AST marker compilation.
- `python3 -B -m unittest discover -s core -p 'test*.py' -q`: **58 tests passed**.
  These include eight offline snapshot-repair tests; they are mirror/helper tests, not the feature suites.
- Exact new overlay rejects wrong and already-repaired preimages; whole-tree check rejects the unrepaired parent.
- Immutable-byte audit: **2381** protected historical files match the baseline.
- Patch-index preflight: **13421** distinct nonzero IDs; three obsolete unavailable preimages
  (`43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`) were not needed by the successful complete replay.
- Private denylist: zero matches across all 211 new/changed core files.
- Gitleaks exceptions are confined to two exact reviewed synthetic fixture lines; no broad rule bypass.
- Full source inventory and immutable historical-byte checks remain mandatory; an apply-only result is not acceptance.

### Final verifier output

```json
{"ancestry_only": 3, "base": "e3bf4be5df04cf72dab4914b073361c71af1dbe1", "bridges": 44, "merge_base": "78abaafcccec20a546c94e8734a70e38c08a6a69", "patches": 2409, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "10a929b4a93d10921b7a16a40bd699e5507ffd8e"}
```

### Mandatory repaired-tree output

```json
{"marker_compile": "pass", "overlay_blob": "36c31384b063be9f57e1992abbea3fa52455dc54", "source_tip": "10a929b4a93d10921b7a16a40bd699e5507ffd8e", "whole_tree": "pass"}
```

The new `repair_hostname_escapes_2409.py` keeps the historical helper unchanged and binds
exact preimage `1437c845d99e6dc336ec3c596570e5e7d3ddd30d` and repaired postimage
`36c31384b063be9f57e1992abbea3fa52455dc54`. Exactly two word boundaries are restored.
The repaired policy changes only the placement applied blob, and the checker rejects unrelated deltas.
Apply this overlay after entry 2409, never in the middle of historical replay.

## Snapshot hashes

- `mirror-policy-2409.json`: `7958479c1843dbe3d15b5d25a2d7b81ec6adc2cd52e1dc41ede7011c03032105`.
- `mirror-replay-2409.json`: `9023bb7f548040b94a586e2e3951d07f2ad7b7e5fce5c437b425f1cddfb7a3f9`.
- `mirror-policy-2409-escape-repair.json`: `f1fb05c475b0e1136d841975b75676231860ee29401bb8914ee76b1dc66650ce`.

## Reproduction and limits

Run [README Applying](README.md#applying), including the final overlay and checker.
Source feature tests, deployment, live host behavior, full application builds and platform suites
were not rerun by this mirror. Their owners retain implementation/deployment acceptance.
This report does not claim historical event backfill or runtime feature certification.
