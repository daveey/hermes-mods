# Additive hostname regex escape repair (2253)

## Behavior

The current Applying recipe requires a post-replay overlay. It restores the leading
word boundary in the two `mini-1` patterns in `hermes_cli/kanban_placement.py`
(the default table and title-only set). Published mails, bridges, policies and the
historical verifier are unchanged. Applying the mails alone retains the defect.
This is not a new source commit or a new contiguous mirror ordinal.

## Why it failed

Problem: the historical placement blob's default pattern `r"\mini-1\b"` raises
`re.error: bad escape \m at position 0`.

1. The generic hostname rule consumes the `b` in a preceding `\b` as part of a
   hostname. Evidence: `test_word_boundary_normalization_and_semantics` runs the
   shipped policy on the public fixture `\bsams-mac-mini\b` and gets `\mini-1\b`.
2. The rule operates on bytes without distinguishing regex syntax from hostname
   text. Evidence: `verify_mirror.normalize` and the hostname-family rules in
   `mirror-policy-2253.json`.
3. Whole-tree equivalence accepts reviewed historical blob pairs; it does not
   compile regexes. Evidence: the unchanged 2253 verifier passes while the AST
   marker guard rejects its exact applied placement blob.

**Root (happened):** context-free hostname normalization treats regex escapes as
hostname characters. **Root (not caught):** existing normalization tests covered
plain hostname text, not escaped regex values; tree-fidelity proof is not semantic
validation. **Recurrence:** the same applied blob is pinned in policies 2156 and
2253; the 2253 handoff explicitly retained it to keep published bytes immutable.

Countermeasures and proof:
- Symptom: exact-preimage overlay, exactly two replacements, exact postimage check.
  Actual README replay executed the CLI and whole-tree checker successfully.
- Root: `normalize_regex` protects boundary tokens in regex values before applying
  privacy substitutions. It is **not** a general source-file/Windows-path scrubber:
  callers must supply regex values, not ambiguous text such as `C:\bob-mbp`.
  No automatic exporter integration is claimed (this repository has no exporter).
- Detection: `validate_markers` parses the default table without importing source,
  resolves constant-string names and compiles each regex; missing/nonliteral tables
  fail closed. New export workflows must call this guard on the normalized module.
  It is not a whole-program regex analyzer. Tests cover corruption rejection and
  positive/negative word-boundary matching, case, `.local`, serialized escapes,
  idempotence, protocol literals, pre/postimage rejection and symlink refusal.

## Applying and checking

Run the complete [README Applying recipe](README.md#applying), including the final
mandatory overlay and check. The initial unchanged verifier proves inventory,
ancestry-only entries and historical tree fidelity. The final
`check_hostname_escape_repair.py` proves the repaired whole tree with the unchanged
`verify_mirror.compare` implementation, and compiles the default marker table.
The new policy differs only in the reviewed placement postimage; the checker
rejects any other policy change. Do not use this new policy to verify unmodified
historical replay: it must reject that tree.

- Source tip: `9bc6ed6b6df403e0c80529753b9814d3ec119cba`.
- Before blob: `2be6b779c96825ea64a86120851eb484ec866e83`.
- After blob: `fa4cceb74abf284504a725d3e1ad5b7421fdc570`.
- Source blob remains `b231594fd0387081ae73cb7f98e77b4a8835a70a`.

The overlay intentionally refuses reruns, changed preimages and other snapshots.
Apply it only to a disposable replay checkout after 2253, not to the live fork.
The next mirror owner must explicitly carry the repair forward and re-prove its
resulting tree; do not insert this final-state overlay before historical patches
that still require the old context.

## Executed tests

- Initial regression run: nonzero (13 subtest failures) with the legacy normalizer;
  the corrected normalizer passes.
- AST guard on the actual historical blob: `PatternError: bad escape \m at position 0`;
  repaired blob compiles.
- `python3 -m unittest discover -s core -p 'test*.py'`: 50 tests passed.
- Unchanged full CLI verifier: 2253 patches, 41 bridges, 3 ancestry-only entries, exit 0.
- Actual README Bash recipe (including another full verifier run): exit 0; overlay
  commit changes exactly two lines in one file. Final check:

```json
{"marker_compile": "pass", "overlay_blob": "fa4cceb74abf284504a725d3e1ad5b7421fdc570", "source_tip": "9bc6ed6b6df403e0c80529753b9814d3ec119cba", "whole_tree": "pass"}
```

- Final checker on the same replay's unrepaired parent: exit 2 (rejected).
- `git diff` confirms published patches, bridges, original policies/replay plans
  and `verify_mirror.py` unchanged.

These are mirror, normalizer and targeted marker proofs, not runtime deployment
or a run of all mirrored feature suites. Source object prerequisites remain those
in the README; no missing-object or fingerprint check was bypassed.
