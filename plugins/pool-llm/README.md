# pool-llm

Run script, eval, judge and batch LLM calls on the Hermes subscription account pool (the Claude Max `anthropic` and
ChatGPT `openai-codex` OAuth accounts in `hermes auth list`) instead of paid API keys, and stop agents from asking
the user to buy API credit before they tried it.

## Helper: `bin/hermes-pool-llm`

Re-execs `pool_llm.py` under the Hermes runtime interpreter (`hermes --print-runtime-command`), so it works from any
shell or venv.

```bash
B=~/.hermes/plugins/pool-llm/bin/hermes-pool-llm
$B models                                                    # subscription models the pool serves (managed routing)
$B check                                                     # one ping per pool, exit 0 when both answer
$B ask --provider anthropic --model claude-sonnet-5-5 "hi"   # text on stdout
$B ask --provider openai --tier fast --json - < prompt.txt   # {text, provider, model, route}
$B serve --port 8765                                         # local SDK-compatible endpoint
```

`serve` lets an unmodified harness use the pool: `ANTHROPIC_BASE_URL=http://127.0.0.1:8765`,
`OPENAI_BASE_URL=http://127.0.0.1:8765/v1`, any non-empty API key. It serves Anthropic `POST /v1/messages` (Claude
Code system prefix and `mcp__` tool names added for the OAuth token, tool names restored in the reply) and OpenAI
`POST /v1/responses` / `POST /v1/chat/completions` on the Codex backend (unsupported keys like `max_output_tokens`
and `temperature` dropped, `store=false`). Non-streaming only. Each reply carries an `x-hermes-pool-route` header
(provider, model, account). Only models the managed routing registry lists as subscription routes are accepted.

On a 429/quota/credit error the account is marked exhausted in the pool and the call retries on the next account;
when all refuse it raises `PoolExhausted` (HTTP 429 from `serve`) naming the accounts tried.

Not covered: embeddings (no pool path) and models the pool doesn't carry; those still need a paid key.

## Hooks

- `transform_terminal_output` / `transform_tool_result` (terminal, execute_code, process): output containing
  "credit balance is too low", "no credits remaining" or `insufficient_quota` gets one line naming the helper.
- `pre_tool_call`: a `kanban_block`, blocked `kanban_create`, `inbox_post` or `action_request` asking the user to add
  or buy API credit (or linking the Anthropic/OpenAI billing pages) is refused once per session when the session
  never ran the helper. Retrying goes through.

The coach plugin's `credit_ask` reply rule is the final-reply half of the same guard.

## Tests

```bash
cd ~/code/hermes-mods/plugins/pool-llm && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```
