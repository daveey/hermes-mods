# Gateway adapter lifecycle kept under its health cap after the 717447e upstream merge

Patches: **1822** (`483ee6badf`, `[daveey] Keep merged adapter lifecycle within health cap`), **1823** (`4af752c4ed`, `[daveey] Extract adapter status methods to preserve merged health budget`).

Mirrored in the 1822–1918 pass ([mirror-1918-verification.md](mirror-1918-verification.md)).

## Behavior

- After the upstream 717447e merge, the gateway adapter lifecycle and status methods live in `gateway/run_adapter_status.py`, so `gateway/run_adapters.py` stays under its code-health size cap without changing adapter behavior (1822/1823).

## How it works

Pure moves: adapter status methods and the merged lifecycle code are extracted into a sibling module that the facade late-imports.

Source commit notes (first paragraph of each message, privacy-normalized):

- 1822: [daveey] Keep merged adapter lifecycle within health cap
- 1823: [daveey] Extract adapter status methods to preserve merged health budget

## Code

`gateway/run_adapter_status.py`, `gateway/run_adapters.py`, `gateway/run_startup.py`.

## Tests

Landed with the source commits; not rerun by this mirror pass unless the verification report lists them. Names are tests each patch added; a file with no names was modified only.

- No test files changed in these patches (see Behavior for what is therefore untested here).

## Applying

Apply as part of the full series with the README **Applying** recipe; these patches depend on earlier core files and are not certified standalone. Re-implementing on a newer upstream: port the Code files above and keep the listed tests green.

## Gotchas

- None recorded beyond the source commit messages.

## Plugin?

No: these change core modules that plugins cannot hook.
