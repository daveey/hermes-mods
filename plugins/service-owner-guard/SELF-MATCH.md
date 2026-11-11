# Self-matching process cleanup: cause and prevention

## Observed incident and evidence boundary

The command/path spelling below is anonymized; the original transcript and
durable process-result record remain private.

A tracked shell job began with `pkill -f "fixture-tests/.venv/bin/python -m pytest"`
and then attempted to restart a long test run. The terminal result reported that a
self-termination pattern had been flagged and **auto-approved by smart approval**.
The durable process result was exit `-15` (SIGTERM), no output, no recorded
termination source. The literal pattern also matches the enclosing shell's
`-c` command string. No live destructive reproduction is needed or permitted.
The actual signal sender was not retained: the matching shell explanation is
supported by command semantics, not claimed as proven signal attribution.

## Why-chain

- **Why did a flagged command execute?** The core's
  `tools/terminal_tool.py::_run_approval_guards` accepts `smart_approved` and
  `tools/approval.py::_smart_gate` returns approved for a guardian `approve`.
  The self-termination entry in `tools/approval_detection.py` is a dangerous
  pattern, not an unconditional veto. The returned audit note identifies this
  route; this was not a user approval or an allowlist approval.
  Its regex scans the entire command: a later output path can supply the word
  `hermes` even when the pkill target does not name the agent. The label is not
  proof that actual process identities were resolved.
- **Why did the guardian approve?** `tools/approval_smart.py::_smart_approve`
  asks an auxiliary LLM for a one-word verdict. The prompt permits benign
  development operations. The incident's reasoning was not retained; its
  particular interpretation is undetermined. Do not infer it from the child exit.
- **Why could a probabilistic verdict miss this foot-gun?** There was no
  deterministic shell-self-match check. Existing service-owner policy concerns
  marked ownership, not a future enclosing shell process created after preflight.
- **Why was it not caught?** Existing owner-guard tests covered process ownership,
  remote selection and signal normalization, not an unowned command matching
  its own shell. New inert hook-level terminal and execute_code regressions
  failed on unchanged code before the fix.
- **Why did the workflow invite it?** A long suite ran in the foreground, timed
  out, and cleanup/relaunch were combined with a pattern kill. A timeout does
  not establish descendant termination or authorize another broad signal.

**Root (happened):** deterministic, predictable shell self-match was delegated
solely to smart risk assessment and owner lookup, neither of which guaranteed
rejection. Historical signal attribution remains undetermined.

**Root (not caught):** missing hook-level regression for this unowned shell
shape and for the same literal terminal call carried through execute_code.

**Recurrence:** repository history and cross-gateway task-title searches for
`pkill`, `self-termination`, `termination` and `approval` were checked. No earlier
same-chain owner/fix was found. Prior service-owner changes covered remote target
resolution and fixture ownership isolation, not shell self-match. This bounded
search is not proof that no occurrence exists anywhere.

## Countermeasure and boundaries

Use the existing service-owner-guard pre-tool veto, before owner discovery or
smart approval, for local unscoped `pkill -f` / `--full` whose simple pattern
matches its enclosing shell command. Reuse existing shell/action parsing.
Allow only the small regex alphabet of ASCII letters/digits, underscore, dot,
slash, space, colon and hyphen, with at most 512 characters, so Python and POSIX
ERE semantics agree without running an arbitrary complex regex.

No new plugin, no core monkeypatch and no blanket kill ban. Exact PID cleanup,
process-name selection, signal-zero probes, anchored/bracket expressions and
narrowing selectors retain existing owner policy. Shell wrappers parsed by the
existing extractor are included. SSH/remote full matches, complex expressions,
dynamic Python and direct subprocess calls are **not** new coverage.

For execute_code, only literal `terminal(command=...)` / positional terminal
calls (including attribute form) are checked here. Inert Python strings are not
mistaken for this new shell rule. Nested tool_call terminal calls are checked.
This is a best-effort guard, not a security sandbox. Existing opt-out still applies.

## Safe cleanup lesson

Start potentially long tests using `terminal(background=True)` and retain the
returned process ID. Observe with `process_manage poll/wait/log`; stop with
`process_manage kill` for that exact tracked job. In a subagent, wait or explicitly
hand off the owned job before exit.

If no registry handle survives, inspect the candidate PID's full argv, ownership,
parentage and start time. Revalidate identity immediately before sending a signal
to that exact PID; a stale PID can be reused. Never combine pattern cleanup and
relaunch in one shell command. A matched pattern or terminal timeout is not
ownership proof. Do not use broad process matching as an alternative.

## Tests and delivery status

`tests/test_self_match.py` passes commands only as inert data to the real
pre-tool hook, with subprocess execution forbidden by a tripwire. It exercises
terminal, literal-terminal execute_code and nested tool_call entry paths, plus
non-target controls. Before implementation: **30 failed, 15 passed**.
After implementation and additional controls, the canonical plugin suite passed
**84 tests and 9 subtests**; repository guards passed **21 tests, 2 skipped**
(managed Python 3.14.9). Canonical verification is:

```
HERMES_MODS_PYTHON=<managed test python> scripts/test-all -k service-owner-guard --hermes-core <core checkout>
```

That runs the owner suite and repository guards. A branch/test receipt is not a
live-deployment claim. Independent review is required before merge; no unsafe
live rejection probe is part of acceptance.
