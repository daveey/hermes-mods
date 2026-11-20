# Lifecycle guard: startup ENV, expanded names, aliases, and process-substitution stdin

Patches: **4570–4583, 4597** (5011 snapshot).

## Behavior

The lifecycle guard walks explicit shell startup environment files (`ENV` / assignment-prefix wrappers) before child payloads, without evaluating dynamic names. Unresolved wrapper cwd, clustered/abbreviated chdir flags, and reversed or quoted env names fail closed or are recognized conservatively. Commands with neither `=` nor `$` skip extra startup tokenizing.

Name-taking builtins, namerefs, indirect parameter operators, and expanded unset can hide subscripts in earlier assignments; those payloads are scanned. Integer context is retained from alias declarations, alias-invocation `-i`, and option words assembled by trailing-space alias expansion, without evaluating aliases.

Process-substitution stdin into nested login shells (`su -l` / `bash -l < <(cat f)`) is walked like a file redirect. Non-login non-interactive `bash < <(cat f)` stays session-scoped.

Heredoc execution oracles no longer depend on GNU `timeout`. POSIX `-ic $ENV` real-shell premises follow Bash 4+ versus Bash 3.2 data.

## How it works

`lifecycle_guard_startup` selects the startup variable from shell options, iterates startup sources with the invoking cwd, and canonicalizes GNU env cwd flags. Nested walks refuse unresolved chdir rather than reading a same-named file in the caller directory.

`lifecycle_guard_arith_words` recovers expanded names and all indirect operator families. Alias values are dequoted assignment words. Sticky integer hints come from alias definitions and option-only alias values.

Nested-shell classification uses contextual segments that resolve `< <(cat f)` into `< f`, not flat views that keep only the `<(...)` placeholder.

## Code

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_startup.py`
- `cron/lifecycle_guard_arith_words.py`
- `cron/lifecycle_guard_output.py`

## Tests

**None executed in this documentation pass.** The following test paths were read from the assigned normalized patches; they are shipped coverage, not fresh pass results. A renamed path is listed as it appears in its patch. Implementation reports and mutation-oriented test names are not evidence of a mirror rerun.

- `tests/cron/test_lifecycle_guard_login_heredoc_data.py`
- `tests/cron/test_lifecycle_guard_startup_env.py`
- `tests/cron/test_lifecycle_guard_name_indirection.py`
- `tests/cron/test_lifecycle_guard_arith_int_context.py`
- `tests/cron/test_lifecycle_guard_session_aliases.py`

## Applying

Use the **full ordered 5011 snapshot recipe** in [core/README.md](README.md#applying), including its replay bridges and **mandatory post-replay repair overlay and repaired-tree verification**. These ordinals are documentation groupings, not independent cherry-pick sets. Do not apply only this feature's patches or omit the repair overlay.

## Gotchas

The scanner still flags POSIX `-ic $ENV` on every host because some hosts run Bash 4+. Bash 3.2 data results are pinned; scanner refusals stay. Plain `read x` is inert. Unknown expanded options fail towards scanning. `+i` / `-x` alias twins remain allowed. Delayed nameref assignment loops are out of this range.

## plugin?

No. This changes core behavior and cannot be installed as a standalone plugin.
