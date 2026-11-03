# Traceback attribution

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Pitfalls

- For execute_code traceback alerts, recover the complete decoded tool envelope and originating cell before grouping incidents. Bundled Python lives under `.hermes/tools/python-*/lib`, so a generic `/tools/` frame match misattributes stdlib failures to Hermes; executor frames above `<cell>` likewise do not prove a core defect. Test raw, dict, JSON and guidance-suffixed envelopes, qualified exception tails, genuine core calls below the cell boundary, and successful cells quoting historical tracebacks. Replay stored results into an isolated sink, not the live defect ledger.
