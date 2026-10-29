# perf(skills): stat-keyed frontmatter cache for skill lookup (kanban admission 20s timeouts) (#176)

Patch 0927, source `1bab43d41f186b1185fe7ac5de22425a0d9626e2`.

## Behavior

perf(skills): stat-keyed frontmatter cache for skill lookup (kanban admission 20s timeouts) (#176). This is the source commit's bounded change; later patches may refine it.

## How it works

The patch changes the following implementation seams (diff hunk headings, not a claim that every seam has independent test coverage):

- `+"""_safe_frontmatter memoizes path parses without hiding edits, deletions or collisions."""`
- `file-serving helpers shared with the local-skill path. Helpers tests patch on th`
- `def _truncate_description(description: str) -> str:`

### Source design notes

The source author's rationale below is retained for porting. Any tests or live observations quoted here are source claims, not executions by this mirror:

> * perf(skills): memoize SKILL.md frontmatter parses by file stat so kanban admission stops re-parsing the whole catalog per pinned skill
>
> * fix(skills): bind frontmatter cache entry to the fstat of the bytes read; keep parser object in entry
>
> * fix(skills): preserve universal newline decoding in cached reads
>
> * test(ci): add session_move to prompt-surface snapshot (main red since 59009a4979)
>
> (cherry picked from commit 7e621eb9ba611a942c1052b7f953eaeddd9f83d3)
>
> * fix(telemetry): v4 schema lists the session_move toolset [daveey]
>
> 59009a4979 added the session_move toolset, which joins
> shared_metrics_contract.TOOLSET_NAMES, but the v4 schema's
> tool_enabled_unused toolset enum was not updated, so
> test_v3_schema_accepts_exactly_the_contract_values fails on main.
>
> * fix(ci): document kanban.create_overlap_check; LSP spawn test fake carries pid [daveey]
>
> Main went red on two tests: d0cc66e9f4 added the kanban.create_overlap_check
> config key without docs (docs drift ratchet), and 47ae53b158 made LSPClient._spawn
> read proc.pid, which the windows-hide-flags test fake lacked.

## Code

- `tools/skills_tool_plugin.py`

## Tests

Source test files changed by this commit:

- `tests/tools/test_safe_frontmatter_cache.py`

New or changed test definitions present in the source diff:

- `test_unchanged_file_parsed_once`
- `test_path_read_preserves_universal_newlines`
- `test_edit_invalidates`
- `test_deletion_returns_empty`
- `test_result_is_private_copy`
- `test_alias_collision_still_detected_from_cache`
- `test_work_bound_across_repeated_lookups`
- `test_symlink_retarget_mid_lookup_does_not_poison_old_target`
- `test_successive_patched_parsers_do_not_share_entries`

These are source coverage references, not a claim that this mirror executed the suite. Only results explicitly recorded in [mirror-958-verification.md](mirror-958-verification.md) were run for this publication. Other entry paths are untested by this mirror.

## Applying

Apply the complete ordered series from the documented merge-base with `git am -3 --empty=keep`. Patches share state and are not independent cherry-picks. See the linked patch for the full diff; use its implementation seams above when porting to a different upstream revision.

## Gotchas

Patch numbers now follow fork-log ordinals, not the older curated numbering. Privacy substitutions use example users, accounts and hosts. Merge-only resolutions and duplicate handling are listed in the verification document. Do not infer a live deployment or full test-suite pass from successful patch application.

## Plugin?

This entry mirrors an in-tree fork change. It is not a separately installable hermes-mods plugin.
