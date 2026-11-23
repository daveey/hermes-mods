# Snapshot 5923 verification

Frozen source: `02ad53634badad70acff02e4b835df00f46b5d2b`.
This append publishes patches **5837–5923** (87 mails) and 33 feature documents.
Existing published patches and bridges were not rewritten.

## Full inventory and replay

The unchanged `core/verify_mirror.py` CLI exited 0 with the frozen policy and
replay plan. Its exact output was:

```json
{"ancestry_only": 3, "base": "ea405e49a5e281558b3eeb3a13af66c6116cd0f2", "bridges": 51, "merge_base": "b46e21495f6a1d5a2547e9f196b3e6fec041ea46", "patches": 5923, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "02ad53634badad70acff02e4b835df00f46b5d2b"}
```

The complete published snapshot 5836 also passed before the append. The final
5923 run used policies frozen after privacy corrections; no verifier changes or
new replay bridges were needed. Inventory checks proved contiguous ordinals,
unique SHA headers, exact non-merge source coverage and one mbox separator per
mail. All 5,886 previously published patch/bridge files remain byte-identical.

## Actual Applying recipe and mandatory repair

The README's segmented Applying recipe was separately executed with Git 2.51,
including all 51 bridges, three ancestry-only markers, and the snapshot-bound
hostname escape repair. The initial verifier invocation was not duplicated in
that recipe run because the full CLI above had already completed. The recipe
exited 0 and its final checker printed:

```json
{"marker_compile": "pass", "overlay_blob": "6324662aaa1cf31fb3863d5a3b4b4ccbda095df7", "source_tip": "02ad53634badad70acff02e4b835df00f46b5d2b", "whole_tree": "pass"}
```

Eight snapshot repair unit tests passed. Seven historical abbreviated/preimage
IDs remain unavailable out of 29,686 distinct nonzero patch index IDs. Neither
the baseline nor final full replay nor the actual recipe needed those objects;
this is not a claim that every historical preimage object is present.

## Privacy and independent review

The full private denylist scan of changed contents and filenames reported zero
hits. The staged privacy hook and coverage checker passed. Separate-session
independent review reproduced all 87 normalized source diffs and the composed
inherited/new exact-line fingerprint transformations, reviewed the README and
33 feature documents, and approved the corrected staged tree. Incident-derived
fixture identifiers, records, prose and live measurements were replaced while
preserving tested semantics. Pseudonym mappings were checked for injectivity
and collisions. Private mappings and source-derived audit files are not part
of this publication.

One narrowly scoped gitleaks exception covers an explicitly synthetic disposable
IPC fixture token in patch 5878; it is not a credential or broad rule bypass.
Published historical mail bytes, verifier code, checker code and bridge bytes
were unchanged. Whitespace checks apply to documents, JSON and Python, not mail
context/trailers or bridge diffs.

## Frozen artifact SHA-256

| File | SHA-256 |
| --- | --- |
| `mirror-policy-5923.json` | `39e7b2ff5692d553c92a113f76d91ddfff4378ac7e14dc7458a8c7fb141d14be` |
| `mirror-policy-5923-escape-repair.json` | `2af4411de9663a7fb53b7fca730ac2507c024f35d0720f3b46d0040b3d825da8` |
| `mirror-replay-5923.json` | `6f665b1044998116f88bd9d8e67f17947307ec324d9640565f4d83f5509585d5` |

## Scope limits

This is mirror inventory, privacy and replay verification, not a fresh execution
of every mirrored implementation test. Feature documents identify source test
coverage; source-suite execution and live performance acceptance are not claimed
by this mirror pass. No Desktop timeout or hotkey change is introduced here.
