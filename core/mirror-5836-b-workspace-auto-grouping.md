# Exclude internal and temporary automatic workspaces

Source patch ordinals: 5550–5551.

## Behavior

Automatic sidebar grouping does not create projects from internal Hermes directories or temporary workspaces, including symlinked profile homes. Explicit user projects remain honored.

## How it works

Workspace classification canonicalizes paths and applies one never-a-workspace policy to Git roots and non-Git session cwds. It includes active/default profile homes, sibling profile homes and OS temporary roots. The tree builder keeps excluded cwd sessions homeless rather than auto-bucketing them; project discovery and resolved roots pass through the same path policy.

## Code

- `tui_gateway/methods_projects.py`
- `tui_gateway/project_tree.py`

## Tests

Source test coverage only; **NOT executed this mirror pass**. No test result or deployment verification is claimed.

`tests/tui_gateway/test_project_non_workspace.py` and `test_projects_rpc.py` cover excluded paths and RPC project-tree behavior. `tests/hermes_cli/test_web_server_chat_workspaces.py` isolates temporary-root policy for the dashboard project/discovered-repository test, so pytest's own temp location is not mistaken for a user workspace.

## Applying

Apply the project RPC and tree-builder changes together. Keep explicit-project behavior separate from automatic discovery. Replay the complete ordered mirror using the snapshot's README/policy recipe; this document is not a standalone patch-application guarantee.

## Gotchas

Canonical paths matter: checking the spelling of a profile directory misses symlink targets. Tests that intend to model an ordinary repository must control the temporary-directory classifier.

## plugin?

No. Automatic project discovery and grouping are gateway core behavior.
