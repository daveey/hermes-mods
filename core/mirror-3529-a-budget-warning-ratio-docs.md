# Docs: one-shot budget warning default and turn-cap requirement

Patches: **3509–3510**.

## Behavior
- The configuration guide now says one-shot runs (`hermes chat -q`, `hermes -z`) and delegate children inside one default `agent.budget_warning_ratio` to 80% when unset; interactive runs stay off, Kanban workers keep 90%, an explicit ratio wins.
- The 80% default only applies with a finite turn cap (`--max-turns` or `agent.max_turns`); with unlimited turns a plain one-shot gets no checkpoint. Subagents always have a cap via `delegation.max_iterations`.

## How it works
3509 and 3510 each edit one paragraph of the configuration page. Docs only.

## Code
- `website/docs/user-guide/configuration.md`

## Tests

Feature tests were **not executed by this mirror pass**. The following source test files and test names were read from the normalized patches; they describe coverage, not fresh pass results. Source-commit RED/GREEN or performance reports are not mirror-run evidence.

No test files in these patches.

## Applying
Apply in series order (see README § Applying); these patches are not independent of earlier entries.

## Gotchas
The behavior described comes from the patch messages and is not covered by tests here.
