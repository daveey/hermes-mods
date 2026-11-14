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
- A new guard plugin must enter private `home/plugins-every-host.txt` in the same change that adds it.
- **`hermes-sync` never commits here.** It only pulls, pushes commits you already made, and links.
  Uncommitted edits stay local (and are logged) until you commit them.

## Work in a detached worktree, never the live checkout

`~/code/hermes-mods` is a deployment checkout loaded by running agents. Do not edit it (including
through plugin/skill symlinks), commit, stash, reset, rebase, or restore files there.

```sh
git -C ~/code/hermes-mods fetch origin
git -C ~/code/hermes-mods worktree add --detach ~/code/mods-work origin/main
cd ~/code/mods-work
# edit and run targeted tests here; stage only your paths, commit, then publish:
git push origin HEAD:main
# If rejected: fetch/rebase in this worktree and retry; never stash the live tree.
git -C ~/code/hermes-mods worktree remove ~/code/mods-work
```

Sync deploys published commits; a manual `git merge --ff-only origin/main` is allowed only on a clean
live checkout. `live-checkout-guard` enforces common file/shell tool paths (not an OS sandbox).
`skill_manage` remains allowed. With `live-checkout-guard` loaded, successful documentation-only
writes are published from an isolated worktree through the normal privacy hooks and reconciled with
an archive; main CI must be green. Failures preserve the live edit and log the reason. Scripts and
plugin/code edits are never auto-published. Sync itself still never commits: skill-only edits blocking
a fast-forward for over 30 minutes get a host/path recovery card.
To reconcile archived leftovers, use `plugins/live-checkout-guard/reconcile.py --help`: it requires
an applicable archive, unchanged archived bytes, and a target commit already on origin/main.
A new guard also belongs in private `home/plugins-every-host.txt`; export must never rewrite that policy.

## Adding or changing a mod

1. Decide public or private. Public = generally useful, with no personal data (family, finances,
   home network, hostnames/IPs, emails, account IDs) and nothing employer-internal. Otherwise, put
   it in `~/.hermes` as usual; the private repo picks it up.
2. Write it **in the detached worktree** at the same relative paths:
   - new agent plugin: `plugins/<name>/` (`plugin.yaml`, `__init__.py`, tests). Its Desktop half goes in `desktop-plugins/<name>/plugin.js`.
   - new skill: `skills/<category>/<name>/SKILL.md` (write the file directly; `skill_manage create`
     would land it in `~/.hermes/skills`, i.e. the private repo). Patch existing ones with `skill_manage`.
   - moving an existing private mod here: `git mv`-equivalent: copy it in, commit and push, then run
     `~/code/hermes/hermes-sync run`. It replaces the `~/.hermes` copy with a link (the old copy is
     kept in `~/.hermes/.hermes-sync-trash/`) and removes the copy from the private repo.
3. Test the worktree copy before publication; verify linked registration after deployment. The whole-repo gate is `scripts/test-all`
   (exit 0 = every suite passed; `-k <plugin>` for one). Python selection is `$HERMES_MODS_PYTHON`,
   then `<core>/.venv/bin/python`, then production (macOS signed Python 3.14; Linux gateway launcher's
   managed Python), then the invoking interpreter with a warning in both header and summary. If that
   invoking interpreter is older than the Python core gates its runtime dependencies on (3.14 today), the
   runner exits 2 before building a site or running any suite: set `HERMES_MODS_PYTHON`. The header
   prints the chosen version; test dependencies are cached per Python minor version. Never a bare `pytest`
   at the root. On the MacBook run
   only the targeted suite (`-k`); the full run belongs on the hub. A new plugin's or skill's tests (`skills/<cat>/<name>/{scripts,tests}/test_*.py`, one suite per skill,
   CI: `.github/workflows/skill-tests.yml`) are picked up automatically, and CI runs every suite a change touches
   (`.github/workflows/plugin-suites.yml`, Python 3.11 + 3.14, no core: core-backed tests must skip there, not error); one with a non-pytest runner gets an entry in `SUITE_OVERRIDES` in that script.
4. Before publishing to `main`, `.githooks/pre-push` validates the actual pushed revision with the same `scripts/ci-plugin-suites` selector as CI and runs the selected whole suites via `scripts/test-all --no-core --exact -k <suite>`. A failure or the 240-second gate budget refuses the push (inside sync's 300-second timeout); never substitute only touched test files. Doc-only fast path (push gate only, `select --doc-fast-path`): when every path in the range is a skill document (`SKILL.md`, or `.md` below `references/`/`templates/`/`assets/`, regular non-executable blob), the owning skill suite is skipped unless one of its non-markdown files mentions `.md`, `SKILL` or those directory names; CI still runs the full selection after the push. Any other path keeps the full gate. This lets `live-checkout-guard` publish SKILL.md edits of skills with suites inside its 45-second budget. Emergency only: `HERMES_MODS_SKIP_PUSH_SUITES='<reason>' git push …` bypasses with a visible `EMERGENCY BYPASS` log.
5. `git add` + `git commit` (the pre-commit hook runs gitleaks and the private denylist; enable it
   with `git config core.hooksPath .githooks` if `hermes-sync` hasn't yet). Then `git push`, or let
   the next `hermes-sync` run push it.
   For a large selection, run `scripts/pre-push-suites --prepare origin/main HEAD` first on the hub. It permits up to one hour and saves a one-hour local success receipt for that exact base/head and gate code; the push reuses it, while a changed range, expired receipt, failure or changed gate reruns/refuses. Fetch before preparing; on a busy main set `HERMES_MODS_PREPARE_JOBS=<n>` (default 4, `--prepare` only) so it finishes before main moves. `--no-core` uses an isolated home and clean interpreter environment; do not run core-backed whole suites on the MacBook.
6. New backend plugin → enable it with `hermes plugins enable <name>` (recorded in the private repo).
   Update the tables in `README.md`.
7. Other machines pick it up on their next `hermes-sync` pass (≤10 min).

- Core patches (`core/patches/NNNN-*.patch`) ship with their feature doc in the same commit: a new or extended
  `core/<feature>.md` and the patch number in its `core/README.md` row. The pre-commit hook
  (`core/check_coverage.py`) refuses a commit that leaves any patch without a row.
- A feature doc may only claim what its tests prove. Each Behavior bullet that names an entry path (terminal,
  execute_code, tool, REST, Desktop...) needs a test for that path listed under `## Tests`. Don't widen a claim
  ("terminal or execute_code") without adding the test for the new path; mark an untested path "untested".

## PR landings are a separate gate

The normal fast-forward direct-push exception below does **not** authorize unreviewed PR merges.
A PR landing requires independent approval of its exact current head and successful configured required
checks; when no required checks are configured, **all** checks and commit statuses on that head must
succeed. Missing, pending, failed, cancelled, skipped/neutral, unreadable or stale evidence holds landing.
An independently completed Kanban review with verified execution/session custody is valid when the
GitHub account cannot approve its own PR; a COMMENT review or a pasted verdict is not approval.

Use `python3 plugins/push-guard/land.py --pr <number> --head <full-sha>` from the deployed checkout
(or its absolute path); add `--review-task <done-card> --implementation-task <implementation-card>`
for the installed Kanban review validator. The helper merges immediately with an exact-head REST pin
and reads the PR back. Raw CLI/REST/GraphQL/auto-merge calls are held by push-guard, even without a
local push-guards.json. No server permissions change; the hook is agent-tool defense in depth, not
an OS sandbox or an atomic server-side review/base lock. Direct pushes retain existing local suite
gates and green-main-CI obligations. See `plugins/push-guard/README.md` for limits and tests.

## Never

- Commit secrets, tokens, `auth.json`/`.env` content, or anything matched by the private denylist.
  Do not bypass the hook with `--no-verify`.
- Commit runtime churn (`status-cache.json`, logs, `__pycache__`, `node_modules`). See `.gitignore`.
- Force-push `main`. History is public, so a leaked value needs rotating, not just deleting. A GitHub ruleset
  (`protect-main-history`) refuses force-pushes and deletion of `main`; normal fast-forward pushes need no PR
  or review. Each host's `hermes-sync` records the hermes-mods commit it loads, and the BB hub files a
  report-only Issue if a host loads a commit not on `main` or `main` is rewritten.
