# Move-to-project creates the folder the gateway lacks

Patch **0681** (`69c52e9402`).

## Behavior

Moving a session into a merged project lane whose folder does not exist on that session's gateway no longer fails with "working directory does not exist".

- The fast path still tries the lane's folder first.
- A refused folder falls through to `ensureProjectRootOn`. The session's own profile reuses its same-named project (creating that project's folder when that is the missing one) or gets the project created at the home-relative folder.
- `homeRelativePath` maps `/Users/<u>`, `/home/<u>`, `/root` and `/opt/data` onto `~`. A path that only shares the `/opt` prefix (`/opt/database`) is left absolute.
- Any other move error is still surfaced.

## How it works

`moveSessionToProject` (`apps/desktop/src/store/projects.ts`) catches the missing-folder refusal and calls `ensureProjectRootOn`, which uses `makeFolderOn` / `createProjectFolderOn` on the session's own profile. Container homes rooted at `/opt/data` are rewritten to the session profile's home the same way a foreign `/Users/<u>` or `/home/<u>` path already was.

## Code

`apps/desktop/src/store/projects.ts` (`homeRelativePath`, `ensureProjectRootOn`, `makeFolderOn`, `createProjectFolderOn`, `moveSessionToProject`).

## Tests

`apps/desktop/src/store/projects-cross-gateway-move.test.ts`, describe `moveSessionToProject into a lane whose folder this gateway lacks`:

- `creates the project in the session's home instead of failing`
- `makes the folder of the session profile's own project at that path`
- `still surfaces any other move error`
- `homeRelativePath('/opt/data/code/...')` maps to `~/code/...`; `/opt/database` does not.

Desktop vitest was not re-run by this mirror. Live Desktop move against a real missing folder is untested.

## Applying

`git am -3` 0681 after the series through 0680. Touches only `projects.ts` and its cross-gateway move test.

## Gotchas

- The fast path is unchanged when the folder already exists. Only a missing-folder refusal falls through.
- `/opt/data` is treated as a home root. Other `/opt/...` paths are not.

## plugin?

No. The move runs in the Desktop store, before the gateway RPC, which a plugin cannot intercept.
