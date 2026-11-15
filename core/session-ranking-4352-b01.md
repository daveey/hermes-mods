# Session roster single-flight and incremental ranking

Patches: **4248–4251, 4293, 4307** (4352 snapshot).

## Behavior

Concurrent equal-signature roster misses share one fill; returned metadata remains independently copied. Session-list, sidebar and project-tree consumers can reuse ranked page IDs while projecting current rows.

Preview and candidate state is invalidated when ordering inputs become untrustworthy. A throughput schema-heal failure remains eligible for retry rather than being treated as a completed heal.

## How it works

Roster flights are keyed by memo identity and input signature; invalidation prevents an older fill from storing. Same-thread reentry computes directly, and failure or a bounded wait lets joiners compute independently.

RankMemo tracks the session-change cursor, message watermark and rank-lowering triggers. Dirty sessions, ancestors and direct children are reranked; a removed or lowered boundary row forces exact ranking. Transcript-revision previews are snapshot-fenced; the follow-up also binds previews to the row revision visible in the reader snapshot and resets candidate caches. Profile fanout reserves enough resident stores. Benchmark follow-ups use UTF-8 output, narrow accounting exceptions and a module-level enable flag.

## Code

- `hermes_cli/web_routers/profiles.py`
- `hermes_state_common.py`
- `hermes_state_rank_memo.py`
- `hermes_state_recency.py`
- `hermes_state_schema.py`
- `scripts/benchmark_rank_memo.py`
- `tui_gateway/profile_roster_cache.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_state/test_rank_memo.py`
- `tests/hermes_state/test_schema_init_memo_heals.py`
- `tests/tui_gateway/test_profile_roster_cache_singleflight.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Missing signatures do not coalesce. Rank caching assumes AUTOINCREMENT message IDs and ordinary supported writes, not arbitrary explicit-old-ID inserts or REPLACE. Missing trigger coverage uses exact reads. No speedup or benchmark result was measured here; route-level end-to-end performance is untested this pass.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
