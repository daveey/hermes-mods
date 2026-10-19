# system-one

A shared client for System One decision models ([TypeSafe Jev](https://docs.typesafe.ai)). A System One model returns
typed answers (yes/no probability, score or choice) instead of text, in about 0.2 s and at roughly 1/100 of a small
LLM's cost. Other plugins use it for quick judgments and keep their LLM for anything that has to write text.

```python
c = <load plugins/system-one/client.py>      # see coach/system_one.py: client()
ans = c.decide("my_decision", {"message": text}, {"urgent": c.noul("Does `message` need attention now?")})
p = c.p(ans, "urgent")                       # None when unavailable: keep your LLM path
```

- **Fails open.** No key, timeout (`SYSTEM_ONE_TIMEOUT`, default 1.5 s), HTTP or shape error: `decide` returns `None`.
- **Modes per decision:** `SYSTEM_ONE_MODE_<NAME>` or `SYSTEM_ONE_MODE` is `on`, `shadow` or `off`. Each caller picks
  its own default.
- **Log:** `<HERMES_HOME>/system-one/log.jsonl` (or `SYSTEM_ONE_LOG`) gets one line per call: name, latency, tokens
  and the numeric answers, plus the caller's shadow pairs (Jev score vs LLM score). It never records the state or
  the key.
- **Key:** `TYPESAFE_API_KEY` from the environment, else from `<HERMES_HOME>/.env`, so a running backend picks up a
  key added later.
- **Model:** `SYSTEM_ONE_MODEL`, default `jev-1.14.0`.

## Users

| Plugin | Decision | Default |
|---|---|---|
| coach | screen before the fix-loop and hand-off/confirm LLM judges | on |
| prompt-coach | screen before the stage-1 LLM | on |
| topic-shift | new-task detection | shadow |

## Tests

```bash
cd plugins/system-one && uv run --with pytest python -m pytest -q -p no:cacheprovider tests
```
