# Mirror 2015 verification

Appends five full source patches, **2011–2015**, through frozen tip
`2a6c4ae61b9c13e847cfb320a3e609fcc32ea5f8`. Features and coverage limits:
[mirror-2015-features.md](mirror-2015-features.md). Published patches 1–2010,
older policies, older replay plans and the verifier remain unchanged.

## Full inventory, replay and tree

The unchanged verifier exited **0**, including contiguous ordinals, unique
headers, equality with the complete non-merge source set, bridge hashes,
ancestry-only proofs and final normalized whole-tree equivalence:

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 23, "merge_base": "c55af2c93b9d61f5db7943fbc5ff50553038171f", "patches": 2015, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "2a6c4ae61b9c13e847cfb320a3e609fcc32ea5f8"}
```

The README Applying Bash recipe was extracted and executed unchanged, including
its verifier invocation, and exited **0**. Its resulting commit
`492063329337f20ded4d660159abd4ea9807c9b7` separately passed
`verify_mirror.compare` with the same policy. The final slices are `2000:10`,
bridge, `2010:4`, bridge, `2014:1`.

Index-object preflight checked **11,532** distinct nonzero patch-index IDs.
The same four historical abbreviations as snapshot 2010 remain unavailable:
`43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, `a3701890ce1`. They are not in the
new batch, and successful unchanged full replay did not need their three-way
preimages. This is not a claim that every historical blob is available.

## Integration bridges and normalization

Two additional hash-pinned bridges are necessary:

- Before 2011, `bridges/upstream-0dfd1965f18.diff` is the first-parent
  integration diff of `0dfd1965f18`, excluding the two files separately changed
  by patches 2011 and 2012. Its source-merge ancestry anchor is `8733bf9d511`,
  the upstream integration actually ancestral to patch 2011; the later merge
  commit is not an ancestor of that branch patch. Independent review confirmed
  that these are exactly the upstream changes from `82c453a` to `8733bf9`,
  including its resolved conftest change. The intervening mainline commits
  share no files with the bridge and their changes remain intact.
- Before 2015, `bridges/upstream-0f099072b6b.diff` is that merge's exact
  first-parent diff, including the upstream redaction integration. The scratch
  retention commit shares no files with it.

Neither bridge needed privacy substitutions. The policy changes only its
snapshot tip and the existing wait-check fingerprint pair. The latter was
re-pinned after proving that the source/applied difference remains exactly the
previous reviewed example-address substitution; no new normalization rule was
added. Patch bodies match fresh source `format-patch` output byte-for-byte;
only their author headers are normalized.

### Why the first replay failed

Problem: the append-only candidate applied but failed final tree inventory.
Why: upstream modules and tests were absent, as the diagnostic tree-set diff
showed. Why: the five non-merge patch additions do not encode the two upstream
merge deltas (`0dfd1965f18`, `0f099072b6b`). Root (happened): carrying the old
replay plan forward by tip alone without first inventorying merges omitted
required integration content. Root (not caught): the non-merge header inventory
cannot detect merge content; the mandatory whole-tree verifier **did** catch it
before publication. Recurrence: earlier integration bridges in snapshots 2004
and 1918 establish this class; their documented procedure remains necessary.
Countermeasure: inspect merges as well as missing headers, add explicit reviewed
bridges, and execute both full verifier and README recipe. Proof: the unchanged
checks failed before the two bridges and pass after them. No checker was relaxed.

## Targeted execution

Exact source and replayed public trees each passed **132 tests in eight files,
0 failed**, with retries disabled (37.0 s source; 39.1 s applied). Both used
isolated homes and separately provisioned checkout-managed test environments:

```sh
scripts/run_tests.sh -j 1 --file-retries 0 \
  tests/tui_gateway/test_once_compression_lifecycle.py \
  tests/test_session_daemon_reaper.py \
  tests/hermes_cli/test_kanban_wait_live.py \
  tests/plugins/test_kanban_foreign_card_api.py \
  tests/hermes_cli/test_kanban_scratch_owned_jobs.py \
  tests/hermes_cli/test_kanban_workspace_jobs.py \
  tests/hermes_cli/test_kanban_offline_backoff.py \
  tests/hermes_cli/test_kanban_wait_replica.py
```

Native macOS-marked tests in one file were skipped on Linux. No complete
bootstrap-installer workflow, macOS run, full suite, live Desktop transport,
production latency or CPU-remediation outcome is claimed.

## Review and publication gates

Independent Opus review regenerated all five patches, inspected both merge
bridges and the fingerprint change, and reran the full verifier successfully.
It found one gitleaks false positive: an upstream redaction test's synthetic
UUID identifier in the second bridge. A narrowly line-scoped `.gitleaksignore`
entry covers that reviewed fixture; no real credential is excepted. The private
denylist scan reported zero hits in all 13 staged files, including bridges.
The unchanged coverage checker passed against the staged index, documentation/
JSON whitespace checks passed, and the whole-directory gitleaks scan exited 0.
