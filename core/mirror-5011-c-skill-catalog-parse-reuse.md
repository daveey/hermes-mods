# Reuse exact-text skill parses across catalog refreshes

Patches: **4599** (5011 snapshot).

## Behavior

Skill command-catalog refreshes reuse the pure text parse when truncated catalog bytes are unchanged. Due refresh still rechecks policy and parser identity. Custom parsers are never memoized. Retention is bounded; parse errors retry. Cached YAML objects are never lent out.

## How it works

`skill_catalog_parse.parse_catalog_text` keys reuse on exact text plus parser/policy identity (`yaml_loader` included). Body fallback and nested values stay private copies. Real TTL preserves same-stat edit deadlines.

## Code

- `tools/skill_catalog_parse.py`
- `tools/skills_tool.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/tools/test_skill_catalog_parse_reuse.py`
- `tests/tui_gateway/test_catalog_cpu_replay.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

Custom parsers may carry caller scope; preserve invocation semantics. Replay timings are evidence, not a flaky CI gate or whole-RPC live acceptance.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
