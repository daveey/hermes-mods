# Plugin `ctx.dispose_path` guarded disposal seam (mirror 5489, 5499)

## Behavior
- `PluginContext.dispose_path(path: str, *, reason: str, empty_only: bool = False) -> dict` performs one-shot guarded deletion on behalf of a plugin. Outcomes: `disposed`, `missing`, `refused`, `error`. A refusal is final: there is no deletion fallback.
- The path's parent is resolved and its final component is not followed; only paths strictly inside the active `HERMES_HOME` or a top-level `/tmp/hermes-*` directory are eligible, the roots themselves are refused, and write-denied paths are refused, all before any unlink/rmtree. Without `empty_only` a directory is removed recursively. Actor, reason, checked path, outcome, detail and `empty_only` are logged. Non-string arguments, a blank reason or an empty/NUL path raise.
- `empty_only=True` (keyword-only, strict bool) does `lstat` then a single non-recursive `os.rmdir`, refuses non-directories and ENOTEMPTY/EEXIST, and never falls back to recursive delete, so files arriving after observation are not removed.
- The method round-trips JSON-only through the plugin host.

## How it works
Host-owned `hermes_cli/path_disposal.dispose_path` executor; `plugins_context.py` exposes it. Docs: capability row, signature, outcomes, scope in `website/docs/user-guide/features/plugins.md`.

## Code
`hermes_cli/path_disposal.py`, `hermes_cli/plugins_context.py`, `website/docs/user-guide/features/plugins.md`.

## Tests
Source: `tests/hermes_cli/test_plugin_dispose_path.py` (by test names: file/actor logging, directory, missing, outside scope, home root, final symlink, traversal, parent-symlink traversal, write-denied, read-only parent error, OS error text, blank reason, non-string path, tmp root and child, a host-crossing round trip; for `empty_only`: empty and non-empty directory, arrival after observation, symlink, file, missing, scope, non-bool values, OS errors and ENOTEMPTY errno variants, write-denied, tmp root, a single atomic attempt with tripwires against recursion/target scans, and a host-crossing variant). Not executed in this mirror pass.

## Applying
Apply 5489 then 5499 (which modifies the same files).

## Gotchas
No consumer is migrated in these patches (the docs name a disk-cleanup plugin as the first intended consumer); the docs describe the host executor as where custody policy could veto removal, but no veto is implemented in this diff.

## plugin?
Yes, a context API for plugins; the patch itself is core.
