# Wake an ancestry wake_check pinned to a rebased commit

Patches: **1646** (`8d3f19f72d`, `Wake ancestry probes pinned to a rebased commit identity`), **1647** (`4e3dc17dde`,
bound the detector's cleanup wait), **1648** (`d96ebe6ea9`, sanitized UTF-8 subprocesses).

Related: [kanban-condition-wakes.md](kanban-condition-wakes.md) (wake checks in general).

## Behavior

- A card parked with a `wake_check` like `git -C <repo> merge-base --is-ancestor <sha> HEAD` waits for a commit to land.
  If the branch was rebased or cherry-picked before landing, `<sha>` never becomes an ancestor and the card waits
  forever. Now, when such a check exits 1, the scheduler looks (locally, read-only, within 10 s) for a commit on the
  repo's upstream default branch with the same stable patch-id. If it finds one, it wakes the card once with a
  `wake_check_dead_ancestry` event and a dispatcher comment: "wake_check waits for `<sha>`, which landed on
  `<upstream>` as `<landed>`; the original identity is not upstream. Re-park on `<landed>` or run the check now."
- Patch equivalence is evidence to replan, not proof the change is deployed; the card wakes, it does not complete.
- Each (repo, SHA) wakes at most once: re-parking with the same check does not wake again.
- Ordinary waits are unchanged for: an unmerged commit, an absent object, a non-ancestry check, a commit already an
  ancestor of upstream, an exhausted budget, a stale wake generation, or any command shape the parser does not
  recognize.

## How it works

`hermes_cli/kanban_dead_ancestry.py` (new):

- `ancestry_probes(cmd, cwd)` tokenizes with `shlex` (punctuation-aware) and accepts only literal
  `git [-C dir]... merge-base --is-ancestor <hex 7-40> <ref>` at a command start (after `;`, `&&`, `||`, `|`). It
  returns nothing if the command contains `cd`, `pushd`, `eval`, `export`, `$` or a backtick (it does not guess
  shell-mutated cwd or environment); quoted `echo`/`bash -c` contents are not commands. Relative `-C` paths resolve
  against the check's cwd.
- `_landed` resolves the SHA, finds upstream from `refs/remotes/origin/HEAD` (else `origin/main`), returns early if the
  SHA is already an ancestor, requires `git cherry` to mark it `-` (equivalent upstream), computes `git patch-id --stable`
  and streams `git log -p <tip> --not <sha>~1` into one `patch-id` process to find the landed commit. All git calls run
  with `GIT_NO_LAZY_FETCH=1`, `GIT_TERMINAL_PROMPT=0` and never fetch.
- `detect(cmd, cwd)` shares one 10 s budget across probes; any git error, timeout or decode error skips that probe.
- `kanban_wake_check.py` calls `detect` only when the check exited 1 with no output error, no answered ask and no
  producer, and `apply_result` calls `wake_once` inside its generation-checked transaction.
- 1647 bounds the final `log.wait()` in `_find_patch` to 1 s. 1648 builds the git environment with
  `tools.environments.local.build_subprocess_env` (sanitized) and decodes output as UTF-8.

## Code

`hermes_cli/kanban_dead_ancestry.py` (new), `hermes_cli/kanban_wake_check.py`.

## Tests

`tests/hermes_cli/test_kanban_dead_ancestry.py` (1646; real git repos built in `tmp_path`, real scheduler
`run_due_wake_checks`; not rerun by this mirror pass):

- `test_scheduler_rebased_head_and_controls` (`old` wakes once with a comment naming both SHAs and does not re-wake
  after a re-park; `landed`, `unmerged`, `absent`, `non-ancestry` stay scheduled with zero failures).
- `test_detector_default_branch_and_unknowns` (abbreviated SHA, `origin/HEAD` → `trunk`, zero budget, missing upstream,
  non-repo cwd).
- `test_parser_does_not_guess_shell_state` (echo, `cd`, `$REPO`, `bash -c`, unterminated quote).
- `test_parser_multiple_and_relative_paths`, `test_stale_generation_does_not_wake`.

1647 and 1648 add no tests. Untested: a repo whose history exceeds the 10 s budget, Windows, and non-UTF-8 git output.

## Applying

Apply 1646, 1647, 1648 in order. No config.

## Gotchas

- Only the literal command shape above is recognized; a check wrapped in a script or using variables still waits
  forever on a rebased SHA.
- Detection reads remote-tracking refs as they are; with a stale `origin/*` (no fetch) a landed patch is not found.
- The scan walks `git log -p` from upstream back to the SHA's parent: on a very old SHA in a large repo it may run out
  of budget and simply skip.

## Plugin?

No. It runs inside the core wake-check scheduler's transaction.
