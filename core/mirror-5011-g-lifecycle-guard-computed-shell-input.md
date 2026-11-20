# Lifecycle guard: computed shell input (added, narrowed, rolled back)

Mirror entries 4963, 4964, 4965, 4969, 4970.

## Behavior

The gateway-lifecycle guard (`scan_gateway_lifecycle`) scans shell commands for gateway restarts. These entries are a short history:

1. **4963** refuse shell input computed from unresolved command-substitution output (`bash $(echo ./r.sh)`, `eval "$(cat ./r.sh)"`, `bash -c "$(cat ./r.sh)"`, `bash <<< "$(...)"`, empty splices like `b$(true)ash`), failing closed with "the shell input depends on unresolved command substitution output".
2. **4964** narrows it after a replay of historical commands showed ordinary commands newly refused: a computed program with literal options and literal `-c` code (no stdin feed, no other descriptors, no `$`/backtick left) is scanned as shell text; `. "<output>/literal/tail"` resolves the tail against the walk directory and scans that file (a tail with `..`, glob or expansion stays refused); `/abs/dir/<output>` used as a bare word is not a shell invocation.
3. **4965** allows `eval "$(hermes-core-wt env)"` (bare or under the Hermes bin dir, `env` the only argument, whole operand) by reading it as `eval ""`; defining the name as a function/alias or assigning HOME/PATH keeps the refusal.
4. **4969** rolls the whole refusal back as incident containment: quote-harvested fragments and the real core-worktree helper (computed Python selection, piped input) were newly refused, breaking delivery. Behavior returns to the pre-change scanner, including its known fail-open for unresolved substitution output. Reintroduction must pass the unchanged real helper test plus the new compatibility witnesses.
5. **4970** makes the compatibility-witness scripts use portable shebangs.

Net effect after 4969: the new check is gone; `test_lifecycle_guard_core_worktree_compat.py` stays as a witness.

## How it works

Pre-rollback, `cron/lifecycle_guard_substitution_output.py` fails closed when a placeholder (substitution body already removed) sits at a program word, script operand or code argument of bash/sh/source/`.`/eval/`-c`/`<<<`, following variables, descriptors, functions, loops and case arms. A directly invoked file with a literal Python shebang keeps its prior shell walk. 4969 deletes that module's wiring from `lifecycle_guard.py`, nested shells, output, shebang, substitutions and variables.

## Code

`cron/lifecycle_guard.py`, `lifecycle_guard_nested_shells.py`, `lifecycle_guard_output.py`, `lifecycle_guard_shebang.py`, `lifecycle_guard_substitution_output.py`, `lifecycle_guard_substitutions.py`, `lifecycle_guard_variables.py`.

## Tests

Source tests (not executed here): `test_lifecycle_guard_computed_program` (24 refusal and 8 allowed rows, real bash against a fake marker), `_substitution_*`, `_quoted_provenance`, `_direct_python*`, `_interpreter_identity`, `_function_variables`, `_loop_cache_contract`, and `test_lifecycle_guard_core_worktree_compat` (reduced witnesses of the real helper's shapes).

## Applying

Never apply a single entry. Follow the full segmented recipe in the core mods [README](README.md) ("Applying"): the 4963→4965 additions and the 4969 revert must be taken as a unit, in order.

## Gotchas

- Do not reintroduce computed-input hardening without passing real helper-shaped witnesses; the rollback tradeoff is documented as containment, not a final design.
- Known residual: a PATH-shadowed `python3` polyglot with computed shell input is open both before and after.
