# Public mirror: 400-patch candidate

## Snapshot and publication boundary

Source tip: `0146a882edf55e22fc9197e2a2e190a0fbacf068`.
Base: `4957bbd0a0f6a1c3fe8217dc4a66d072971f5b63`.
Applied public commit: `a61785d75bdefa3e9e63601c309c8045cf58f76b`.
Applied public tree: `60057747260313e88f70f190e2f415f12902830d`.
This is a locally verified candidate, not a deployment or publication receipt.
Source 0146a882ed was locally committed and pending independent review when exported;
publication of the source and this mirror must be coordinated separately.

## Merge-history reasoning (0393)

The previous 392 public patches terminate at source `2228cfe8dada6912c5f8966f967d12870d029c2d`.
They remain byte-identical, at their existing paths and numbers, including adapted 0251.
Historical verification documents therefore retain their original numbers and scope.

A raw reverse non-merge export is NOT the apply order for this snapshot. The actionability
branch contains two overlapping implementations (`18c419d2a9` and `6ab333af32`),
paired fixture changes (`53a6693092` and `d7235c1661`), and later integration fixes
(`dd13c9b77c`, `c13f6de33e`, `3d0d4ea6a4`). Its merge commits also carry resolutions
(`341c1ca588`, `5e7b3f828c`, `323c8d17be`, `6d2be1e857`). Replaying only the
non-merge changes both conflicts with mainline and omits those recorded resolutions.
The attempted 405-patch export failed at raw branch patch 0343; it is superseded.

0393 integrates the first-parent delta of the recorded final merge
`2b6644387f36bd0cc9e4c88f6aeb36572f8b4b1d`, whose first parent is the 392-source tip.
It replaces individual replay of all seven branch non-merge commits, not seven independent
features. This preserves the actual merged result rather than choosing one duplicate implementation.
Two files require a further explicit public-baseline adaptation: `hermes_cli/kanban_session_activity.py`
and `tests/hermes_cli/test_kanban_session_activity.py` differ from the source parent in comments,
cache declaration placement and test ordering. They take the recorded merge versions; neither
old public patch bodies nor unrelated scrubbed source files are replaced. The patch envelope
is synthetic and names its source merge rather than impersonating a raw source commit.

0394–0400 are then the source first-parent followups in order:

| Patch | Source | Purpose |
| --- | --- | --- |
| 0394 | d69ad5291ce94014ab67a99a136ad43a2577a850 | Deterministic auto-continue marker timestamp test |
| 0395 | 1330267ab4d0273b04045618ba97a289f1c27d9a | LSP cleanup/reaping test robustness |
| 0396 | a286616b5a0e2eadfdaa97a9030a2247045d42f6 | Atomic activity snapshot projects fixture |
| 0397 | dc50ba33a29bd0237f33bd2bf22a3e4070f5053a | Scope-heartbeat owner liveness |
| 0398 | a253016acf39404f7e0b7aefd4151d09584a176e | Exact owner-attested legacy alias reconciliation |
| 0399 | 76b9c78f24f2d89d22ec45e20b71a2e738d06c70 | Distinct legacy-reconciliation wire action |
| 0400 | 0146a882edf55e22fc9197e2a2e190a0fbacf068 | Preserve sync timestamp during ledger contention |

## Full apply and preservation

A fresh detached worktree at the base successfully ran
`git am -3 --empty=keep /path/to/hermes-mods/core/patches/*.patch` (exit 0),
with exactly 400 commits after the base. No manual conflict resolution was used in
that final full replay. All 392 previous patch files were compared byte-for-byte against
public mods `0d717388de06fdf4fb1b41907a0f8718518010eb`. The full series passes the private
denylist scan. Feature coverage is checked by `python3 -B core/check_coverage.py`.
The public tree is intentionally not identical to the private source tree: existing privacy
scrubs and older documented mirror adaptations remain. Changed Kanban production files
match the source tip, apart from pre-existing blank-line differences in `tools/kanban_tools.py`.

## Tests

On the final applied public series, `scripts/run_tests.sh -j 4` ran these files:

- `tests/hermes_cli/test_kanban_actionability_counts.py`
- `tests/hermes_cli/test_kanban_human_request.py`
- `tests/hermes_cli/test_kanban_human_request_entries.py`
- `tests/hermes_cli/test_kanban_request_lifecycle.py`
- `tests/hermes_cli/test_kanban_graph_human_request.py`
- `tests/hermes_cli/test_kanban_migration_http_auth.py`
- `tests/hermes_cli/test_kanban_session_activity.py`
- `tests/hermes_cli/test_kanban_session_activity_triage.py`
- `tests/hermes_cli/test_kanban_origin_legacy.py`
- `tests/hermes_cli/test_kanban_origin_adapter.py`
- `tests/hermes_cli/test_kanban_origin_projection_contention.py`
- `tests/hermes_cli/test_kanban_origin_scope_heartbeat.py`
- `tests/tui_gateway/test_auto_continue.py`
- `tests/tui_gateway/test_projects_rpc.py`
- `tests/agent/lsp/test_client_e2e.py`

Result: **290 passed, 0 failed, no skips**, 15 isolated file subprocesses.
The packaged PM bootstrap failed (missing packaged runtime); the supported `HERMES_PYTHON`
override used the isolated validation Python 3.11 environment, with FastAPI, HTTPX and
python-multipart installed. Initial missing-dependency runs are not counted as acceptance.
This is targeted regression evidence, not full-suite, Desktop UI, live gateway, deployment,
or real legacy-migration acceptance. No live database was modified.
