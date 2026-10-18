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
   To check "already present", grep a distinctive 4-6 word phrase or identifier (a path, command, test name) across
   the skill dir and sibling sub-skills, not the whole line: later sessions usually re-saved the note reworded.
   A failed call whose operations carry no new text at all is `stale` (nothing to recover).
3. **Oversize (>= 70%)**: split before the cap bites. Back up the old file to the scratch dir first.
   - Keep SKILL.md a router: when-to-use, orientation, the rules that apply to every task in the area, a table
     mapping task -> sub-skill, and a line saying new notes go to a sub-skill/reference and the router stays under
     ~40% of the cap (otherwise it refills and every later save fails again).
   - Move topical sections to sub-skills (`<area>-<topic>`, each "Use when ..." description). Do the move as a
     script that assigns every source line number to exactly one target and asserts no line is unassigned or
     assigned twice; hand-copying a 100 KB file drops bullets silently.
   - Leave `references/` and `scripts/` in the umbrella dir so existing paths, cron prompts and task cards keep
     working; each sub-skill states that dir and `skill_view(name='<umbrella>', file_path='references/<f>')`.
   - While moving, merge exact duplicate rules and label superseded procedures as historical with a pointer to
     the current one; don't rewrite content otherwise.
   - Verify: every non-blank line of the backup is in one of the new files; each new skill loads via
     `skill_view`; a `skill_manage patch` on a sub-skill is accepted.
   - Grep the repo docs for copies of the skill elsewhere (other profiles, remote gateways) and update the
     copy instruction to name all the new dirs.
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

## Scheduling it (cron)

- Two jobs: a daily guard with `--monitor-script` running `--signal` (zero model calls on quiet days), and a
  weekly job with `--script` injecting `--report`. Both attach this skill + the repo-sync skill and tell the agent
  to reply `[SILENT]` when there is nothing to do. Cron `--script` paths must live in `~/.hermes/scripts/`: use a
  thin wrapper that calls this skill's script (and, for a second Hermes home or host, runs it there over ssh with
  `HERMES_HOME=...`, prefixing its lines, dropping errors so the monitor output stays stable).
- Create the jobs on ONE host only: two hosts auditing the same synced skills race each other.
- Pick an explicit `--deliver` target and prove it: trigger once with `hermes cron run <id>`, then check
  `hermes cron list` for "no delivery target resolved". `deliver=all` can resolve to nothing and the result is
  then only in `~/.hermes/cron/output/<id>/`. Check the host's other jobs for the same warning while there.
- A monitor job's first tick is its baseline and wakes the agent with every current item, so trigger it once
  right after creating it: that run clears the backlog and doubles as the end-to-end test.
- The ack file is what stops a handled lost write from re-firing the monitor; the script's "already landed" check
  only catches verbatim text, so reworded or relocated notes still need the agent's judgment and an ack.

## Limits (do not cross unattended)

- Never edit bundled skills (listed in `skills/.bundled_manifest`): upstream owns them and updates overwrite edits.
- Never delete a custom skill or a reference with unique content; archive instead, and say so in the report.
- Don't rewrite a skill another session is actively patching (its `last_patched_at` in `skills/.usage.json` is
  within the last hour): defer it to the next run.
- Moving a mod between the public and private repo is the user's call; flag it, don't do it.
