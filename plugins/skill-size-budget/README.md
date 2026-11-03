# skill-size-budget

Automatic skill edits keep SKILL.md bodies lean; new detail goes to `references/`, one hop deep.

The background self-improvement review and the curator walk patch shared skills after turns. Every load of a skill
pays for its whole body, and those patches only ever add: kanban-orchestrator took 168 automatic patches and reached
52,824 chars. Core's `skill_manage` only warns at 50% of its 100,000-char hard cap, far past the point where a skill
stops being read closely.

## Behavior

`pre_tool_call` on `skill_manage`. The call's operations are simulated in order (core's fuzzy patch matcher, so the
result matches what `skill_manage` would write). For each SKILL.md the call writes, the BODY (text after the
frontmatter) is measured before and after:

- under budget after the edit (default **300 lines and 30,000 chars**): pass;
- not growing (a split in progress, a deletion): pass;
- growing over budget by exactly one line that points to `references/<file>.md`, or by one `- [ ]` line inside the
  skill's `## Done means` section: pass (that is the redirect this plugin asks for);
- a reword that adds no line and at most 120 chars while staying under 30,000 chars: pass;
- anything else: when the skill-ledger actor is `curator` (background review fork, curator walk), the edit is
  **refused** with the recipe: if the skill has `## Done means`, turn a correction into one checklist line there;
  otherwise `write_file references/<topic>.md` and patch a one-line pointer into the step that needs it, in one
  batch (shrinking ops first).

A write to `references/*.md` that adds a link to another reference file (`references/x.md`, or `](x.md)`) is refused
for the curator the same way: SKILL.md links every reference directly, and a rule two hops deep is never read.

Foreground edits (actor `agent`/`user`) are never refused: `transform_tool_result` adds `size_budget_warning` with
the same guidance to the successful result.

Telemetry: one JSON line per checked edit in `<HERMES_HOME>/logs/skill-size-budget.jsonl` (verdict pass / warn /
block, actor, skills, findings).

| Env | Default | |
|---|---|---|
| `SKILL_SIZE_BUDGET` | `on` | `off` = skip; `shadow` = never block, warn + log only |
| `SKILL_SIZE_BUDGET_LINES` | 300 | body line budget |
| `SKILL_SIZE_BUDGET_CHARS` | 30000 | body char budget |
| `SKILL_SIZE_BUDGET_LOG` | | log path override |

## Inventory / one-time split

`inventory.py` lists skills whose body is over a budget (`--lines 500 --chars 30000`), marking upstream-bundled ones.

## Tests

    scripts/test-all -k skill-size-budget

`SKILL_SIZE_BUDGET_TEST_BASELINE=1` replaces both hooks with no-ops (the pre-plugin state) to show the acceptance
tests red.
