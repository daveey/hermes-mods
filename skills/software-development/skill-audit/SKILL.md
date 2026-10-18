---
name: skill-audit
description: "Use when auditing, refactoring or recovering Hermes skills. Size cap, lost notes, overlaps."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [hermes, skills, maintenance, audit, cron]
    related_skills: [hermes-agent-skill-authoring, hermes-mods-repo]
---

# Skill audit: keep skills under the cap, recover lost notes, consolidate

## When to Use

- A scheduled skill-audit / skill-guard cron job fired (its prompt names this skill).
- A `skill_manage` write failed with `SKILL.md content is N characters (limit: 100,000)`.
- The user asks to audit, clean up, consolidate or refactor skills.

## The script

`scripts/skill_audit.py` (stdlib only, reads `$HERMES_HOME`, default `~/.hermes`):

- `--signal [--days 7]`: stable lines, empty when nothing needs attention. `oversize <skill>` = a custom
  SKILL.md at >= 90% of the 100,000-char cap; `lost-write <tool_call_id> <skill>` = a failed `skill_manage`
  call never followed by a successful write to that skill in the same session, whose new text is not in any skill
  file now. Used as a cron `--monitor-script`, so the agent only runs when this output changes.
- `--report`: the full audit (near-cap skills, lost writes, broken `references/` links, unreferenced supporting
  files, name/description overlaps, per-skill usage, weak descriptions, shadowed names).
- Each lost write's original arguments are saved to `$HERMES_HOME/cache/skill-audit/lost-<id>.json`.

## Procedure

1. Run `--report` (or read the injected one). Re-run it at the end to show before/after.
2. **Lost writes first** (they are notes a session meant to keep). For each `lost-<id>.json`: read the
   arguments, check the target skill (and its references and sibling sub-skills) for the same fact in other words,
   and check the live system or repo if the note may be stale. Re-apply what is still true and missing,
   placed in the sub-skill or reference that fits now (the original target may have been split since). Then append
   `<id> applied|superseded|stale: <why>` to `$HERMES_HOME/cache/skill-audit/acked.txt`. Never re-apply blindly.
3. **Oversize (>= 70%)**: split before the cap bites. Keep SKILL.md a router: when-to-use, orientation, the
   rules that apply to every task in the area, a table of sub-skills/references. Move topical sections to
   sub-skills (`<area>-<topic>`, each "Use when ..." description) or `references/<topic>.md`. Verify nothing was
   dropped: every non-blank line of the old file must be in one of the new files (script it). Back up the old file
   to the scratch dir first.
4. **Unreferenced references**: a reference no SKILL.md names is effectively invisible. Link it from the skill
   or sub-skill whose topic it covers (one line: when to read it). Delete only if it is wholly superseded.
5. **Broken links**: fix the path, or remove the mention if the file is gone for good.
6. **Overlaps**: read both skills. Merge only when the triggers really are the same task; otherwise sharpen both
   descriptions so the trigger is unambiguous. When merging, move the content and leave nothing behind (archive the
   absorbed skill with `hermes curator archive <name>` or `skill_manage delete` with `absorbed_into`).
7. **Stale facts**: for skills used in the last week, spot-check 3-5 concrete claims (paths, hostnames, commands,
   "X is fixed in Y") against the live system or repo; fix or mark historical ones.
8. **Descriptions**: first 57 chars self-contained, `Use when <trigger>. <behaviour>.`
9. Finish: commit and sync per `hermes-mods-repo` (public skills in hermes-mods, everything else via
   `hermes-sync run`), then report a short table: what changed, what was recovered, what needs the user.

## Limits (do not cross unattended)

- Never edit bundled skills (listed in `skills/.bundled_manifest`): upstream owns them and updates overwrite edits.
- Never delete a custom skill or a reference with unique content; archive instead, and say so in the report.
- Don't rewrite a skill another session is actively patching (its `last_patched_at` in `skills/.usage.json` is
  within the last hour): defer it to the next run.
- Moving a mod between the public and private repo is the user's call; flag it, don't do it.
