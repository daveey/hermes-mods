# pool-llm

Run script, eval, judge and batch LLM calls on the Hermes subscription account pool (the Claude Max `anthropic` and
ChatGPT `openai-codex` OAuth accounts in `hermes auth list`) instead of paid API keys, and stop agents from asking
the user to buy API credit before they tried it.

## Helper: `bin/hermes-pool-llm`

Re-execs `pool_llm.py` through the installed managed Hermes publication contract,
without executing a PATH-selected discovery command. `binding` reports read-only
selection provenance (no bootstrap, provider call or token read). See
[runtime-binding.md](runtime-binding.md) for the trust boundary and exclusions.

```bash
B=~/.hermes/plugins/pool-llm/bin/hermes-pool-llm
$B models                                                    # subscription models the pool serves (managed routing)
$B check                                                     # one ping per pool, exit 0 when both answer
$B ask --provider anthropic --model claude-sonnet-5-5 "hi"   # text on stdout
$B ask --provider openai --tier fast --json - < prompt.txt   # {text, provider, model, route}
$B serve --port 8765                                         # local SDK-compatible endpoint
eval "$($B env --port 8765)"                                # export URLs + private API token (do not log)
```

`models` lists every eligible subscription route per provider and tier, in registry preference
order (duplicates removed), not just the first model. Any listed model can be requested explicitly.

Without an explicit model, `ask --tier deep` and `complete(..., tier="deep")` try the selected
provider's subscription models in that tier's order. Each model first rotates through its eligible
accounts; on `PoolExhausted`, the next model is tried (for example Fable → Opus). Fallback never
crosses providers or uses metered routes. When all models refuse, the error names every model tried.
`ask` prints one fallback notice on stderr, and `ask --json` / `complete()` include `fallback_from`
(the first refused model, or null when no fallback occurred) and the actual served `model`.
Explicit models (`--model`, `client(model=...)`, or a `serve` request's model) never fall back.
With the managed registry off, the existing `DEFAULT_MODELS` tier choices remain unchanged.
Regression coverage: `tests/test_tier_fallback.py` uses a fake registry, pool and clients, without network.

`ask --json` includes Anthropic's `stop_reason`. A `refusal` (even with text), or
`max_tokens` with empty/whitespace-only text, exits 2 and names the stop reason on stderr;
stdout still contains the result in the requested format. These semantic failures are not
retried on other accounts. Nonempty partial answers remain successful. `complete()` preserves
the stop reason as metadata; this exit-status policy applies to `ask`, not the raw API proxy.
Regression coverage: `tests/test_ask.py` exercises both CLI output modes with mocked provider
responses, including thinking-only failures, successful answers and Codex compatibility.

`serve` lets an unmodified harness use the pool: `eval "$(hermes-pool-llm env)"` exports
(the command also unsets inherited `ANTHROPIC_AUTH_TOKEN` to prevent conflicting SDK headers)
`ANTHROPIC_BASE_URL=http://127.0.0.1:8765`, `OPENAI_BASE_URL=http://127.0.0.1:8765/v1`,
and `ANTHROPIC_API_KEY`/`OPENAI_API_KEY` containing this home's private token. It serves Anthropic `POST /v1/messages` (Claude
Code system prefix and `mcp__` tool names added for the OAuth token, tool names restored in the reply) and OpenAI
`POST /v1/responses` on the Codex backend (unsupported keys like `max_output_tokens`
and `temperature` dropped, `store=false`). `POST /v1/chat/completions` routes `claude*` models to Anthropic
and other models to Codex. Claude chat requests support function `tools`, `tool_choice` (`auto`, `none`,
`required`, or a named function), assistant `tool_calls` history, and `role: "tool"` results.
Replies preserve tool IDs/names, JSON-string `arguments`, text (or null), real token usage including cached
input, and `tool_calls`/`length`/`stop` finish reasons. Consecutive tool results are grouped into one user
message; `parallel_tool_calls: false` disables parallel tool use. Tool-chat content supports text only;
unsupported content or tool types fail explicitly. Codex chat tool requests return HTTP 400
`tools are not supported on this route` (use native `/v1/responses` for Codex tools).
Plain chat requests without tool fields/history keep their legacy response shape and behavior.
This support is on the HTTP serve endpoint, not the text-only `complete()`/`client()` library helpers.
Regression coverage: `tests/test_chat_tools.py`. `POST /v1/embeddings` is forwarded to OpenRouter (key `OPENROUTER_API_KEY` from Hermes's `.env`; a bare model such
as `text-embedding-3-large` becomes `openai/text-embedding-3-large` and the reply names the model as asked; upstream
errors keep their status; the key is never logged). Each reply carries an `x-hermes-pool-route` header
(provider, model, account). Only models the managed routing registry lists as subscription routes are accepted.

### Local request boundary

`serve` accepts only non-browser clients. Every request must carry exactly one `Host`
header matching the bound loopback IP and actual port (for example `127.0.0.1:8765`),
and must not carry an `Origin` header, even an empty value or `null`. DNS aliases
(including `localhost` when the bound address is `127.0.0.1`), wrong/missing ports,
missing/duplicate Host headers, and non-loopback/wildcard bindings are refused with
HTTP 403 before reading JSON or accessing credentials/upstreams. Use the numeric
loopback URL shown above; ordinary Anthropic/OpenAI SDKs supply Host automatically
and do not supply Origin. `ask` and `check` use the library directly and are unchanged.

After those checks, every request (including health and OPTIONS) must authenticate
with `x-api-key: <token>` or `Authorization: Bearer <token>`. Missing, incorrect or
conflicting credentials return a fixed HTTP 401 before reading the body or touching
credentials/upstreams. Both headers may be supplied only with the same correct token.
`serve` and `env` create `<HERMES_HOME>/pool-llm/serve.token` if missing (0700 directory,
0600 exclusive/no-follow file, random `secrets.token_urlsafe(32)` contents). Existing
tokens must be regular non-symlink files, owned by the current uid, with no group/other
permissions; unsafe or invalid files refuse startup. Tokens persist without expiry.
Only `env` deliberately prints the token, as shell exports; never log its output or
enable shell tracing around it. Use the same HERMES_HOME for clients and server.
This blocks browser/rebinding and other local users without the token, not code
already running as your uid that can read your private files. Keep this endpoint on
loopback and do not publish it through a reverse proxy. `ask`, `check` and `models`
do not use HTTP and do not need the token.
Regression coverage: `tests/test_http_boundary.py` uses real loopback HTTP with
fake upstreams; existing ask/check and streaming tests cover client compatibility.

### Streaming Messages

`POST /v1/messages` accepts `stream: true`, including the Anthropic SDK's `messages.stream()`:

```python
import anthropic
client = anthropic.Anthropic()  # after eval "$(hermes-pool-llm env)"; SDK reads URL + token exports
with client.messages.stream(model="claude-haiku-5-5", max_tokens=256,
        messages=[{"role": "user", "content": "Explain server-sent events briefly."}]) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

Events are forwarded as SSE and flushed individually, with tool names restored in
`content_block_start` events. The response uses connection-close framing (no content length).
Quota rotation and auth recovery apply only until the first upstream event is received;
errors after that become an SSE `error` event and close the connection, never a replay on
another account. Before the first event, failures keep the usual JSON HTTP error responses.
OpenAI `/v1/responses` and `/v1/chat/completions`, and embeddings, remain non-streaming;
`stream: true` returns HTTP 400 naming `/v1/messages` as the supported streaming route.
Regression coverage: `tests/test_messages_stream.py` exercises incremental forwarding,
restored tools, pre-event quota/auth recovery, post-event errors, and non-stream compatibility.

On a 429/quota/credit error the account is marked exhausted in the pool and the call retries on the next account;
when all refuse it raises `PoolExhausted` (HTTP 429 from `serve`) naming the accounts tried.
For both Claude and Codex, a 401, `token has been revoked`, or `invalid_grant` first invokes the pool's
locked credential-store resync/OAuth refresh for the issuing account and retries once with a new client.
Persistent auth failure (or failed refresh) marks that account unhealthy with the pool's 401 cooldown and
rotates to the next account. The running daemon keeps the recovered credentials; no restart is needed.
Other errors (for example malformed requests) still propagate without rotation.

Not covered: embeddings (no pool path) and models the pool doesn't carry; those still need a paid key.

## Hooks

- `transform_terminal_output` / `transform_tool_result` (terminal, execute_code, process): output containing
  "credit balance is too low", "no credits remaining" or `insufficient_quota` gets one line naming the helper.
- `pre_tool_call`: a `kanban_block`, blocked `kanban_create`, `inbox_post` or `action_request` asking the user to add
  or buy API credit (or linking the Anthropic/OpenAI billing pages) is refused once per session when the session
  never ran the helper. Retrying goes through. Only LLM provider credit counts (the same `_about_llm_credit` check as
  the coach rule: the billing target nearest the ask, by sentence then paragraph, decides): an ask to raise the
  GitHub Actions spending limit or fund AWS/Vast/Cloudflare passes, while a bare "add $250 of credit" still holds.

The coach plugin's `credit_ask` reply rule is the final-reply half of the same guard.

## Tests

```bash
cd ~/code/hermes-mods/plugins/pool-llm && uv run -q --with pytest python -m pytest -q -p no:cacheprovider tests
```
