# AGENTS.md — rules for agents working in daveey/hermes-mods

**This repo is PUBLIC.** It holds the shareable Hermes mods: agent plugins (`plugins/<name>/`),
Desktop plugins (`desktop-plugins/<name>/`) and skills (`skills/<category>/<name>/`). Everything
else (config, per-host backups, private or employer-specific skills and plugins, tooling) lives in
the private repo `daveey/hermes` (`~/code/hermes`, branch `state`).

## How it's wired on my machines

- Clone: `~/code/hermes-mods` on every Mac. `hermes-sync` (from the private repo, launchd every
  10 min) clones it if missing, fast-forwards it to `origin/main`, pushes local commits that haven't
  been pushed yet, and links every mod into Hermes:
  - `~/.hermes/plugins/<name>` → `~/code/hermes-mods/plugins/<name>` (symlink)
  - `~/.hermes/desktop-plugins/<name>` → `~/code/hermes-mods/desktop-plugins/<name>` (symlink)
  - skills: `skills.external_dirs: [~/code/hermes-mods/skills]` (set from the private repo's `shared-config.json`)
- Because of the symlinks, editing `~/.hermes/plugins/<name>/…` edits this working tree. The same
  goes for `skill_manage` patches to these skills.
- Plugin enable/disable state stays in the private repo (`home/plugins-enabled.txt`).
- **`hermes-sync` never commits here.** It only pulls, pushes commits you already made, and links.
  Uncommitted edits stay local (and are logged) until you commit them.

## Adding or changing a mod

1. Decide public or private. Public = generally useful, with no personal data (family, finances,
   home network, hostnames/IPs, emails, account IDs) and nothing employer-internal. Otherwise, put
   it in `~/.hermes` as usual; the private repo picks it up.
2. Write it **here**:
   - new agent plugin: `plugins/<name>/` (`plugin.yaml`, `__init__.py`, tests). Its Desktop half goes in `desktop-plugins/<name>/plugin.js`.
   - new skill: `skills/<category>/<name>/SKILL.md` (write the file directly; `skill_manage create`
     would land it in `~/.hermes/skills`, i.e. the private repo). Patch existing ones with `skill_manage`.
   - moving an existing private mod here: `git mv`-equivalent: copy it in, commit and push, then run
     `~/code/hermes/hermes-sync run`. It replaces the `~/.hermes` copy with a link (the old copy is
     kept in `~/.hermes/.hermes-sync-trash/`) and removes the copy from the private repo.
3. Test the live copy (it's linked, so the live copy is this tree).
4. `git add` + `git commit` (the pre-commit hook runs gitleaks and the private denylist; enable it
   with `git config core.hooksPath .githooks` if `hermes-sync` hasn't yet). Then `git push`, or let
   the next `hermes-sync` run push it.
5. New backend plugin → enable it with `hermes plugins enable <name>` (recorded in the private repo).
   Update the tables in `README.md`.
6. Other machines pick it up on their next `hermes-sync` pass (≤10 min).

- Core patches (`core/patches/NNNN-*.patch`) ship with their feature doc in the same commit: a new or extended
  `core/<feature>.md` and the patch number in its `core/README.md` row. The pre-commit hook
  (`core/check_coverage.py`) refuses a commit that leaves any patch without a row.
- A feature doc may only claim what its tests prove. Each Behavior bullet that names an entry path (terminal,
  execute_code, tool, REST, Desktop...) needs a test for that path listed under `## Tests`. Don't widen a claim
  ("terminal or execute_code") without adding the test for the new path; mark an untested path "untested".

## Never

- Commit secrets, tokens, `auth.json`/`.env` content, or anything matched by the private denylist.
  Do not bypass the hook with `--no-verify`.
- Commit runtime churn (`status-cache.json`, logs, `__pycache__`, `node_modules`). See `.gitignore`.
- Force-push `main`. History is public, so a leaked value needs rotating, not just deleting. A GitHub ruleset
  (`protect-main-history`) refuses force-pushes and deletion of `main`; normal fast-forward pushes need no PR
  or review. Each host's `hermes-sync` records the hermes-mods commit it loads, and the BB hub files a
  report-only Issue if a host loads a commit not on `main` or `main` is rewritten.
