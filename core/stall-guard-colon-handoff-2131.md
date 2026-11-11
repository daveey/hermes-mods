# Stall guard: trailing colon with no tool call

Patch **2097**.

## Behavior

A reply such as "Reading the card first, together with the PR:" announces a next step and stops. When tools are offered, the turn made no tool call, the text is at most 400 characters, ends in `:` after trailing whitespace and is not inside an open code fence, the stall guard re-prompts. It uses the same bounded counter (max 2 re-prompts) and log line as trailing continue-intent. A colon answer after tool calls, long replies, and unclosed-fence colons do not re-prompt.

## How it works

Detectors moved from `agent_runtime_helpers` to new `agent/stall_tail_detectors.py` (re-exported for existing importers) and gained `trailing_colon_handoff` (even number of fence markers required). `turn_final_response.finish_text_response` applies it alongside `trailing_continue_intent`.

## Code

- `agent/stall_tail_detectors.py` (new), `agent/agent_runtime_helpers.py`, `agent/turn_final_response.py`.

## Tests

**Source regression coverage; not rerun in this documentation pass.** `tests/agent/test_stall_guards.py`: detector catches announced next steps; ignores answers, code and long replies; zero-tool-call colon tail re-prompts; bounded by the continuation cap; colon answers and colons after tool calls do not re-prompt.

## Applying

Apply ordinal(s) 2097 as part of the snapshot-aware full replay described in [README](README.md), in published order, not as independent patches: later patches in the series edit the same files, and the replay plan decides which are carried verbatim and which are re-bridged. Related: [codex-toolless-commentary-turns.md](codex-toolless-commentary-turns.md). No claim is made here about live rollout; this page documents source behaviour only.

## Gotchas

Legitimate short replies ending in ":" also re-prompt, up to the cap. English-oriented heuristic only.

## Could this be a plugin?

Partly: a post-response hook could re-prompt, but the stall-guard counter and final-response path are core, so no plugin hook covers it today.
