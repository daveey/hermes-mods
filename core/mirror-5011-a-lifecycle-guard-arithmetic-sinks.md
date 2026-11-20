# Lifecycle guard: arithmetic-expansion sink scanning (let words, name-taking builtins, integer context)

Mirror snapshot 5011; ordinals 4362, 4363, 4377, 4378, 4379, 4380, 4381, 4382, 4395, 4415, 4416, 4417, 4418, 4419, 4421, 4427, 4451.

Entries:

- **4362**: Detect compound-array and builtin quoted-subscript arithmetic sinks
- **4363**: Make quoted-subscript classification incremental per simple command
- **4377**: shell_arith_sink: a `let` command word hidden by quotes or escapes is still `let`
- **4378**: decode hidden let NUL truncation and long continuations
- **4379**: Recognize both Bash control-NUL let spellings
- **4380**: Scan name-taking builtin and recursive arithmetic subscript payloads
- **4381**: Recover recursive assignments for name-taking subscript sinks
- **4382**: Scan implicit recursive arithmetic and declaration subscripts
- **4395**: Restore executing-context assertion in arithmetic Bash proof
- **4415**: test(cron): preserve Bash 3.2 hidden-let execution premises
- **4416**: fix(cron): carry conservative integer context across shell walks
- **4417**: fix(cron): keep declare assignment words inert without attributes
- **4418**: fix(cron): bound repeated arithmetic word walks
- **4419**: fix(cron): revisit assignments after eval discovers integer attributes
- **4421**: Pin separator-frame isolation from prior command uncertainty
- **4427**: Cover split double-quoted dollar arithmetic sinks
- **4451**: test(cron): pin Bash 3.2/5.3 data shapes in the name-indirection execution proof

## Behavior

Bash evaluates command substitutions inside arithmetic subscripts, so `let 'a[$(f)]=1'`, `declare -i`, `test -v`, `printf -v`, `read`, `unset`, indexed assignments, parameter indices and substring offsets can all run a command. These entries widen what the guard treats as an arithmetic sink while keeping data-only uses allowed:

- Compound-array and quoted builtin subscripts (4362), scoped to command frames with builtin command position resolved first (assignments, options, redirections, continuations); echo data and command introspection stay non-sinks. 4363 makes classification incremental (each source character tokenized O(1) times, command position from at most 64 leading words, quoted compound-array elements treated as values).
- Hidden `let` (4377-4379): quoted, escaped, ANSI-C (`$'...'`) and `$"..."` spellings of the word `let` are decoded, including NUL truncation, both control-NUL spellings and long continuations; operands then open the same let arithmetic frame.
- Name-taking builtins and recursion (4380-4382): operands of builtins that take variable names, recursive arithmetic subscript payloads, declaration NAME subscripts (evaluated even without `-i`), parameter indices, substring offsets, indexed assignments and `[[ -v name[i] ]]`; recursive assignments are recovered so a value stored earlier and evaluated later is scanned.
- Integer context (4416-4419): a sticky per-scan integer-declaration hint survives eval dequoting and expanded/sourced declarations; declare assignment words without attributes stay inert; repeated arithmetic word walks are bounded by skipping unchanged text already scanned; assignments are revisited when eval discovers integer attributes.
- Test alignment (4395, 4415, 4451): execution proofs account for Bash 3.2 and Bash 5.3 differences (missing `test -v`, rejected subscripted `printf -v` names, Bash 5.3 no longer running a substitution in an `unset` subscript) by pinning exit status and diagnostic per version, while scanner assertions stay for every shape. 4421 and 4427 pin separator-frame isolation and split double-quoted dollar sinks.

## How it works

- `tools/shell_arith_sink.py`, `shell_command_position.py`, `shell_prefix_expansions.py` and the new `tools/shell_let_word.py` carry the lexical logic; `cron/lifecycle_guard_arith_words.py` walks name-indirection payloads and sticky context; `lifecycle_guard.py`, `_nested_shells.py` and `_output.py` thread the hints.
- Real-Bash marker tests execute payloads that touch a marker and require both the scanner and the output-sink check to flag them; paired clean/inert controls must stay allowed.

## Code

Shipped non-test paths (union over these entries):

- `cron/lifecycle_guard.py`
- `cron/lifecycle_guard_arith_words.py`
- `cron/lifecycle_guard_nested_shells.py`
- `cron/lifecycle_guard_output.py`
- `tools/shell_arith_sink.py`
- `tools/shell_command_position.py`
- `tools/shell_let_word.py`
- `tools/shell_prefix_expansions.py`

## Tests

Test files shipped by these entries (source-side tests only; none were executed by this documentation pass, so no pass result is claimed here):

- `tests/cron/test_lifecycle_guard_arith_int_context.py`
- `tests/cron/test_lifecycle_guard_arith_let_word.py`
- `tests/cron/test_lifecycle_guard_arith_lexical.py`
- `tests/cron/test_lifecycle_guard_arith_word_quotes.py`
- `tests/cron/test_lifecycle_guard_name_indirection.py`
- `tests/cron/test_lifecycle_guard_subscript_linear.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard1.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard2.py`
- `tests/cron/test_lifecycle_guard_subscript_prefix_shard3.py`
- `tests/cron/test_lifecycle_guard_subscript_quotes.py`
- `tests/cron/test_lifecycle_guard_subscript_sink.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [segmented Applying recipe in core/README.md](README.md#applying): the full ordered patch list (every segment, in order) with `git am -3 --empty=keep`, followed by any repair overlay that README names for the segment. They are not independent patches: later entries edit files that earlier entries create or change, so applying a subset or a single patch on its own is unsupported. Settings introduced are named under Behavior.

## Gotchas

- The scanner is static: shapes needing data flow (a variable assembled across statements) are out of scope unless covered by the recursive-assignment recovery.
- Like the bare token, hidden-`let` detection ignores command position, so it can only widen the walk (fail closed).
- Verdict replays quoted in commit text were captured-row evidence, not live deployment.
