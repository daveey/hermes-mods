# Terminal refusals say nothing ran; scratch hint says it is shared

Patches: **2723**.

## Behavior

- A foreground `terminal` command refused for `nohup`/`disown`/`setsid` wrappers, for `&` backgrounding, or for starting a long-lived server/watch process is rejected as a whole. The refusal message now ends with: `Nothing in this command ran (no file writes, heredocs, cd or setup steps). Re-send all of it: put setup in a foreground call first, or the whole script in the background call.` Existing message prefixes are unchanged, so tooling that matches them (for example `Foreground command uses '&' backgrounding.`) still matches.
- The prompt's scratch-directory line now adds: `shared by every session on this host: give files task-unique names (for example prefix the task id) or use mktemp`.
- Motivation from the commit message: an agent re-sent only the backgrounded tail of a refused command, dropping the heredoc that wrote its script, and ran another session's stale file of the same name from the host-shared scratch directory.

## How it works

`tools/terminal_tool_guards.py` defines `_NOTHING_RAN` and appends it to each of the three `_FOREGROUND_GUIDANCE` messages. `agent/prompt_builder.py` extends the scratch line built in `_local_host_hints`. The prompt-surface snapshots under `tests/fixtures/prompt_surface/` (the per-platform prompt files and the token counts in `surfaces.json`) were regenerated for the longer line.

## Code

`tools/terminal_tool_guards.py`, `agent/prompt_builder.py`, `tests/fixtures/prompt_surface/prompts/*.txt` and `tests/fixtures/prompt_surface/surfaces.json` (regenerated snapshots).

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/tools/test_blocked_command_guidance.py` (new `TestRefusalSaysNothingRan`) and `tests/test_scratch_dir.py`. Entry path: `_foreground_background_guidance(command)` for a heredoc followed by an inline `&` (also checks the `&` prefix and the word `heredocs`), a `nohup` command and a `uvicorn` server command; and `_local_host_hints()` for the scratch line. The `terminal` tool call itself, the prompt-surface snapshot comparison test, and the other refusal paths of the tool are **untested** by this entry.

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

- The prompt text change grows the volatile prompt by about 30 tokens per surface (recorded in the regenerated snapshot).
- The message says nothing ran because the guard rejects before execution; it does not describe other refusal reasons.

## Plugin

Core change; no plugin required.
