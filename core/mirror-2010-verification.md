# Mirror 2010 verification

This snapshot appends patches **2006–2010**, five complete source diffs after
`221510a25b32673c6f678f383ce15201b6f1c444`, through frozen tip
`79e38bd675dc56816606cf0736123bbf45dacfdd`. The four features and their precise
coverage limits are documented in [mirror-2010-features.md](mirror-2010-features.md).

## Inventory, replay and tree

The unchanged `core/verify_mirror.py` exited **0**, checking contiguous ordinals,
unique SHA headers, equality with the full non-merge source set, every replay
bridge and ancestry-only entry, and final normalized tree equivalence. Its output:

```json
{"ancestry_only": 3, "base": "84a0301415edd6d1385207ec96e4d7f2b6c1fcd9", "bridges": 21, "merge_base": "e26e0e4b67bfae31eddaaefee87c062efd67429b", "patches": 2010, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "79e38bd675dc56816606cf0736123bbf45dacfdd"}
```

The README Applying Bash recipe was separately extracted and executed unchanged,
including its full verifier invocation. It exited **0**. Its final slice is
`2000:10`, covering patches 2001–2010 without changing any bridge boundary.
The recipe-applied commit `96d9e0909460e7a768af983ca464693433221f19` separately
passed `verify_mirror.compare` against the frozen source with the same policy.

The 2010 policy and replay plan differ from their 2005 predecessors **only** in
`source_tip`. All published patches, historical policies, 21 bridges, three
ancestry-only entries, normalization rules, fingerprints and checker bytes are
unchanged. No merge occurs in this five-commit extension; no new bridge is needed.

Object preflight checked 11,513 unique nonzero patch-index IDs. Four historical
abbreviations were unavailable: `43edb3e3f09`, `8274bdeb3db`, `9ff9444f10`, and
`a3701890ce1`. None is in the new batch. Both full replays succeeded unchanged,
so those old preimages were not needed for three-way fallback in this snapshot.
This is not a claim that every historical index object is available.

## Targeted execution

Both the exact source tree and the README-applied tree passed **278 tests across
14 files, 0 failed**, with retries disabled (159.0 s source, 158.4 s applied).
The invocation is `scripts/run_tests.sh -j 1 --file-retries 0` with separate
isolated homes and checkout-managed test environments. The fourteen files are:

- `tests/tools/test_kanban_post_run_calls.py`
- `tests/tools/test_kanban_tools.py`
- `tests/hermes_cli/test_kanban_refused_op_comments.py`
- `tests/hermes_cli/test_kanban_queue_transient_debounce.py`
- `tests/hermes_cli/test_kanban_pull_attempt_timing.py`
- `tests/hermes_cli/test_kanban_pull_attempt_timing_edges.py`
- `tests/hermes_cli/test_kanban_queue_error_provenance.py`
- `tests/hermes_cli/test_kanban_queue_error_timing.py`
- `tests/hermes_cli/test_kanban_queued_reserve_throttle.py`
- `tests/hermes_cli/test_kanban_replica_read_memo_heartbeat.py`
- `tests/hermes_cli/test_kanban_replica_read_memo.py`
- `tests/gateway/test_api_server_bound_port.py`
- `tests/gateway/test_api_crash_recovery.py`
- `tests/gateway/test_api_crash_collision_probe.py`

No full-suite, Desktop, native-platform or live production endpoint test is
claimed. In particular, mirror verification is **not** a live p95 measurement or
latency-acceptance verdict. Untested entry paths remain explicitly labelled in
the feature document.

## Review and privacy

An independent source-to-patch review found that each regenerated new patch
matches its source format-patch output except for the normalized author header.
It confirmed that policy/replay changes are snapshot bindings only, with no
historical patch rewrite. Its first pass found anchored README links that the
coverage checker does not recognize, plus a stale heartbeat-cutoff statement in
an older doc. The links were changed to plain document links and both older memo
documents now point to the superseding behavior. The unchanged coverage checker
then exited **0**.

The private denylist scan found zero hits in every staged file before final
publication. Documentation/JSON whitespace checks and the full pre-commit gate
exited **0**. No privacy exception was added for this batch. The final independent
review approved publication after checking the corrected links, supersession notes,
policy bindings, raw execution logs, privacy scan and staged coverage.
