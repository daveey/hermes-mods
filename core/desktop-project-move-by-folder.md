# Desktop: move into a project the gateway knows by another name

Patches: **1803** (`49345a5b62`, `fix(desktop): move into a project whose folder the gateway knows under another name [daveey]`).

Mirrored in the 1703–1821 pass ([mirror-1821-verification.md](mirror-1821-verification.md)).

## Behavior

- Moving a session into a project whose folder the target gateway already owns under another project name reuses that project instead of failing.

## How it works

`apps/desktop/src/store/projects.ts`, `projects-move-paths.ts`.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1803: Moving a hub session into the Mac's 'infra' project (~/code/focus) failed with 'folder already belongs to project focus': the hub has that folder as project 'focus', the same-name lookup missed it, and projects.create refused the duplicate. Reuse the project that owns the folder instead.

## Code

`apps/desktop/src/store/projects-move-paths.ts`, `apps/desktop/src/store/projects.ts`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- `apps/desktop/src/store/projects-cross-gateway-move.test.ts`: `reuses the remote`, `still surfaces any other projects.create error`

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
