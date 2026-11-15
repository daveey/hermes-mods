# Origin-content annotation holder keys

Patches: **4298, 4320–4322** (4352 snapshot).

## Behavior

Annotation holders can answer unchanged display state using an origin-content narrow key rather than invalidating on raw board/shadow churn. Relevant ownership, membership, status, dependency and freshness-verdict changes still invalidate it.

Unreadable shadow freshness remains unknown instead of being treated as usable cached state.

## How it works

Own-board rows are incrementally patched with per-task revision inputs and reduced to the origin profile's owned/shepherded cards and open ancestors. Sync shadows include linked origin rows and stale verdicts, not every liveness tick. Waiting-source code/registration identity is keyed without executing waiting probes.

Digest-mismatch regressions exercise decodable payloads and inode replacement. A benchmark follow-up charges quiet post-floor narrow probes, not merely exact-fingerprint changes.

## Code

- `hermes_cli/kanban_annotation_host.py`
- `hermes_cli/kanban_annotation_local.py`
- `hermes_cli/kanban_annotation_narrow.py`
- `hermes_cli/kanban_chat_links.py`
- `scripts/benchmark_kanban_annotation_holder.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/hermes_cli/test_kanban_annotation_holder_benchmark.py`
- `tests/hermes_cli/test_kanban_annotation_holder_narrow.py`
- `tests/hermes_cli/test_kanban_annotation_host.py`
- `tests/hermes_cli/test_kanban_annotation_host_split.py`
- `tests/hermes_cli/test_kanban_annotation_narrow.py`
- `tests/hermes_cli/test_kanban_annotation_narrow_local.py`

## Applying

Use the **full ordered 4352 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Unknown narrow keys fall back to exact input evaluation; a readable payload with the wrong digest is not adopted. Freshness is keyed by verdict transitions, not arbitrary clock buckets. No benchmark speedup or live-holder result is claimed here.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
