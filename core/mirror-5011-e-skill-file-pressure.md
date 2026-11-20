# Supporting skill-file size warnings and recovery

Mirror entries: 4794.

## Behavior

Growing supporting skill files receive pressure warnings near the size cap and file-specific recovery advice when a write would exceed it.

## How it works

Warning attachment now covers file_path patches and supporting write_file operations as well as SKILL.md. Warnings compare prior and proposed sizes, naming the actual file and largest sections. Cap errors recommend ordered split/shrink/retry operations in a single atomic batch. For scripts or structured data, the guidance requires valid syntax and caller/reference updates rather than substituting prose pointers.

## Code

- `tools/skill_manager_tool.py`

Paths above refer to the source tree represented by the complete mirror, not files added by this documentation. Historical deletions and later fixes are described explicitly where relevant.

## Tests

Skill-size tests cover growing reference warnings, quiet equal-size/shrinking rewrites, cap refusal, atomic rollback and content-preserving split recovery.

Source test coverage is described from the normalized patches; these tests were **not freshly executed** for this documentation pass. Test paths (including historical paths removed by later entries):

- `tests/tools/test_skill_size_limits.py`

## Applying

Use the **full segmented recipe in [the core README Applying section](README.md#applying)**, including its required bridges, repair overlays and verification steps. These entries are part of that ordered mirror; do not apply this feature group or its individual patches independently.

## Gotchas

The hard cap is unchanged. The tool does not automatically relocate user-owned content, and an advisory warning after a successful write is not a failed write.
