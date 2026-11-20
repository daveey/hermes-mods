# Lifecycle guard: scripts fed through carried file descriptors

Mirror entries 4972–4978.

## Behavior

The lifecycle guard scans a script fed to bash/python through a redirection. Earlier it handled `bash 3<r.sh <&3`; sibling forms still passed unscanned. Now scanned:

- move and dup forms: `bash 3<r.sh <&3-`, `0>&3`, chains `4<&3 <&4`;
- descriptors opened by a redirection-only `exec` and read later on the same or later lines, in pipes, groups or subshells (`exec 3<r.sh; bash <&3`, `exec <r.sh; bash <&0`);
- command-position redirections (`3<r.sh bash <&3`).
Still allowed: reading before open, opening later, clean scripts, a descriptor closed or re-pointed, `bash 3<r.sh >&3`.

Refinements: 4973 keeps conditional exec alternatives and wrapped exec bindings (never discard reachable intermediate alternatives); 4974 keeps lexical keyword identity so quoted `fi`/`done`/`}` aren't treated as scope closers; 4975 keeps parent descriptors across asynchronous AND/OR lists (an `&` backgrounds the whole list); 4976 drops a redundant quote-unaware line after a rebase; 4977 keeps original descriptor copies for nested-stdin provenance; 4978 gates `|&` assertions on system Bash >= 4 so Bash 3.2 still runs the portable cases.

## How it works

New `cron/lifecycle_guard_fds.py` holds `REDIRECTION`, `split_redirections` and one descriptor table (`apply_redirection`): `<`/`<>` open, `<&N`, `<&N-` and `0>&N` copy (a move also closes N), anything else clears. `carry_exec_descriptors` carries the table across lines of one command (`_iter_segment_contexts`), rewriting a later `<&N` to `< f`; fail closed (the file is only scanned) inside subshells or branches. `_command_segment_views` adds an in-order hoist of command-position redirections ahead of the existing end-hoist. `ShellWord` keeps quoted/escaped provenance (`lifecycle_guard_words.py`). Nested shells keep original fd copies.

## Code

`cron/lifecycle_guard.py`, `lifecycle_guard_fds.py`, `lifecycle_guard_words.py`, `lifecycle_guard_nested_shells.py`.

## Tests

Source tests (not executed here): `test_lifecycle_guard_descriptor_carry`, `_descriptor_keywords`, `_descriptor_async`, `_descriptor_provenance`, `test_descriptor_execution_gates`. They run harmless real-Bash markers plus clean controls; the commit messages report mutation checks.

## Applying

Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); these seven entries depend on each other and on the pre-existing guard.

## Gotchas

- `lifecycle_guard.py` sits at its line cap, which is why logic lives in the new module.
- Carry views enumerate possible file inputs, not reaching-definition proofs: over-blocking is accepted.
- Tests need real system Bash behavior; macOS Bash 3.2 lacks `|&`.
