# Hostname normalization of the 1923 mirror

## Behavior

Public patches and documentation use generic laptop and mini identifiers rather
than machine-specific names. `hermes-hub` remains unchanged: it is a placement
alias used by the implementation, not a private workstation name.

## How it works

`verify_mirror.py` accepts optional `regex_substitutions`, applied after the existing
literal substitutions. Patterns operate on bytes; replacement strings are literal
(not backreference templates). Invalid patterns, malformed rules, duplicate
patterns and zero-width matches fail closed. The policy uses case-insensitive
hostname-family rules, preserving `.local` suffixes and surrounding content.

This explicitly authorized privacy repair normalizes historical patch bodies and
docs in place without changing source SHA headers, ordinals or Git history.
Older policies receive the same rules because they verify the same published
patches. Existing fingerprint source IDs are unchanged; applied IDs are re-pinned
only to the hash of the previous applied blob after hostname normalization.
No fingerprint exceptions are added. Replay plans and bridge bytes are unchanged.

## Tests

- `python3 -B core/test_verify_mirror.py`: 28 tests pass, including case and `.local`
  variants, unrelated hosts and protocol literals, literal replacement semantics,
  malformed policies, replay acceptance and rejection of unrelated content changes.
- A private audit compares every modified historical mail/doc with the previous
  published bytes after precisely the policy transformation, and every changed
  fingerprint with the normalized previous blob: 60 files, 143 policy entries,
  zero changed bridges, zero changed renames.
- Full replay of the 1923 snapshot, shared bare source clone, policy and replay plan
  `core/mirror-{policy,replay}-1923.json`: exit 0, output:

```json
{"ancestry_only": 3, "base": "71736c9f32597bbca9d89cede5aa136e55946b2f", "bridges": 16, "merge_base": "a66e74dc67097219b6bb1807d18459859125be62", "patches": 1923, "replay_start": "831637fe8d58f050e9aa776b621bb2c7b614dcd1", "source_tip": "ea651aab330ef51dc3ad565c763c993a0e654e11"}
```

Historical snapshots were re-pinned by deterministic normalization, not individually
replayed in this repair; the latest full snapshot above was replayed and tree-compared.

## Applying / future mirrors

Use the existing README replay recipe and the newest policy. Apply its regex
normalizations as well as its literal substitutions when exporting new mail patches;
never place the private source hostnames in public policy files or tests. The private
publication denylist is a separate gate and must scan case-insensitively.
