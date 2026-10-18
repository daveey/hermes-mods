# Evidence gate for background skill review

Patch: `patches/0129-daveey-background-review-optional-evidence-gate-for-.patch`.

What you see: with `auxiliary.background_review.require_evidence: true`, the background review stops being told
that "most sessions produce at least one skill update" and that a pass doing nothing is "a missed learning
opportunity". It edits a skill only when a specific moment in the conversation shows why (a failed command and the
fix that worked, a user correction, a step that cost time), and "Nothing to save." counts as a normal result.
Skills grow more slowly and every rule in them traces back to something that happened.

Idea from Google's Harness² (https://github.com/google-research/harness2, `harness2/core/prompts.py`
`_KEEP_BASE` + `aggregate()`): an edit is justified by an observed moment, a defect seen once is noise, and
"keep the base" is a valid outcome.

## Behavior

- Config key `auxiliary.background_review.require_evidence` (bool). Default `false`: the prompts are
  byte-for-byte upstream's.
- When `true`, the skill-only and the combined (memory + skills) review prompts change in two places:
  - The "Be ACTIVE — ... not a neutral outcome." sentence becomes the evidence gate: update a skill only when a
    SPECIFIC moment shows the mechanism; name that moment before writing; "looks like good practice" is not
    evidence; a failure seen once that did not reproduce is noise; "Nothing to save." is neutral, and an
    unsupported edit is a regression because it costs context on every later load; when you add always-on
    SKILL.md text, look for something to cut.
  - The closing line that said "Nothing to save." "should NOT be the default" (skill-only) or "don't reach for
    that conclusion as a default" (combined) becomes "say 'Nothing to save.' and stop" when nothing has evidence
    behind it. Without this the prompt would contradict its own gate.
- The memory-only prompt never changes.
- `_LESSON_LAYER_BLOCK` stays as is: the evidence is for deciding whether to edit, not text to write into the skill.
- Applies to automatic reviews and `/refine` alike (the `/refine` focus is appended after the gated prompt).

## How it works

`agent/background_review.py`:

- `_EVIDENCE_GATE` holds the gate text; `_BE_ACTIVE_RE` matches the "Be ACTIVE ... not a neutral outcome."
  sentence in either prompt; `_GATED_CLOSINGS` pairs each closing line with its replacement.
- `build_review_prompt(name, base, require_evidence)` returns `base` unchanged when the flag is off or `name` is
  `_MEMORY_REVIEW_PROMPT`; otherwise it substitutes the sentence and the closings. The prompt constants are not
  forked or edited, so upstream prompt changes merge cleanly, and a text that no longer matches is left as-is.
- `_require_evidence(task_cfg)` reads the flag through `_background_review_task_config` (the pre-loaded
  `auxiliary.background_review` block) with `is_truthy_value`, and returns `False` on any error.
- `spawn_background_review_thread` picks the prompt name from `_PROMPT_NAME_BY_SCOPE` as before, takes the
  per-agent override or module constant (`run_agent.py` exposes the constants as `AIAgent` class attributes),
  and passes it through `build_review_prompt` before appending the memory budget note and `/refine` focus.

`hermes_cli/config_defaults.py`: `"require_evidence": False` in the `auxiliary.background_review` default.

## Code

- `agent/background_review.py`: `_EVIDENCE_GATE`, `_BE_ACTIVE_RE`, `_GATED_CLOSINGS`, `_require_evidence`,
  `build_review_prompt`, and the call in `spawn_background_review_thread`.
- `hermes_cli/config_defaults.py`: default value.
- `tests/agent/test_background_review_evidence_gate.py`.

## Tests

`tests/agent/test_background_review_evidence_gate.py` (6 cases; all pass on the full 0001–0129 series applied to
the fork merge-base, October 18, 2026 PT), each driving `spawn_background_review_thread`:

- `test_flag_unset_prompts_are_upstream_constants`: with `{}` and `{"require_evidence": False}`, the skill-only
  and combined prompts equal `_SKILL_REVIEW_PROMPT` / `_COMBINED_REVIEW_PROMPT` exactly.
- `test_skill_only_prompt_gated`: skill-only entry path; contains the gate, no "Be ACTIVE", no old closing.
- `test_combined_prompt_gated`: combined entry path; same checks.
- `test_memory_only_prompt_unchanged_when_gated`: memory-only prompt is identical with the flag on and off.
- `test_gate_keeps_lesson_layer_block`, `test_gate_does_not_touch_constants`.

Upstream's `tests/agent/test_refine_focus.py` is untouched and passes.

```sh
scripts/run_tests.sh tests/agent/test_background_review_evidence_gate.py tests/agent/test_refine_focus.py
```

## Applying

Apply in series order (`0001–0129`) to the fork's merge-base. The patch touches only
`agent/background_review.py`, `hermes_cli/config_defaults.py` and one new test file, so it should also apply alone
to a nearby upstream; isolated application is not verified. Then set the key:

```sh
hermes config set auxiliary.background_review.require_evidence true
```

## Gotchas

- Already-running sessions keep the old prompt until the core update containing this patch reaches their backend.
- If upstream rewords the "Be ACTIVE" sentence or the closing lines, the substitution silently stops matching and
  the upstream prompt is used. `test_skill_only_prompt_gated` / `test_combined_prompt_gated` fail when that
  happens, so rerun them after every upstream merge.
- The "Signals to look for (any one of these warrants action)" list is unchanged; the gate decides whether a
  signal really happened in this conversation.

## Plugin?

No. The review prompt is chosen inside `spawn_background_review_thread` and passed straight to the fork; no hook
sees or can rewrite it.
