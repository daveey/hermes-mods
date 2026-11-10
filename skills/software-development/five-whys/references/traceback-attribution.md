# Traceback attribution

Reference for skill `five-whys`: its SKILL.md links this file from the step that needs it.

## Pitfalls

- For terminal traceback alerts, check the decoded exit status before attributing stdout to the tool. Successful log/source reads can contain real Hermes frames; prefixed log excerpts and truncated pytest output may lack a standalone exception identity. Never group these under a generic `terminal: traceback` fallback. Preserve explicit error metadata and pipeline-failure hints, and test both successful diagnostic reads and failed core commands through the observer. A classifier fix does not establish that every historical exception printed in those logs was repaired.

- For execute_code traceback alerts, recover the complete decoded tool envelope and originating cell before grouping incidents. Bundled Python lives under `.hermes/tools/python-*/lib`, so a generic `/tools/` frame match misattributes stdlib failures to Hermes; executor frames above `<cell>` likewise do not prove a core defect. Test raw, dict, JSON and guidance-suffixed envelopes, qualified exception tails, genuine core calls below the cell boundary, and successful cells quoting historical tracebacks. Replay stored results into an isolated sink, not the live defect ledger.
