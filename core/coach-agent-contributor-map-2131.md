# Coach agent contributor attribution map

Patch **2079**.

## Behavior

Adds one file under `contributors/emails/` that maps an automation (coach agent) author identity to the owner's contributor name, with a comment saying it works on owner-assigned tasks. Commits by that identity are then attributed like the owner's in contributor tooling.

## How it works

The `contributors/emails/<identity>` convention is a one-line-name file (plus optional `#` comment). No runtime code reads it in this patch; it is data consumed by existing contributor-attribution checks.

## Code

- `contributors/emails/<coach identity>`: two lines (name, comment). The identity string is intentionally not reproduced here because it embeds a private host name.

## Tests

**Source regression coverage; not rerun in this documentation pass.** None added. The file is data only; attribution consumers' own tests are outside this patch.

## Applying

Apply ordinal(s) 2079 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. On a fork, substitute your own automation identity and owner name; the entry is meaningless elsewhere. No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Public-repo privacy: the identity names a private host, so treat any copy of this file as private data and do not publish it in a public tree without renaming.

## Could this be a plugin?

No; it is repository metadata, not behaviour.
