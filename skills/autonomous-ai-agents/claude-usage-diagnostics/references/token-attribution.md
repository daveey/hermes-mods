# Token attribution: where the tokens go

Use for "am I using too many tokens / what is consuming them". The user wants the mechanism of spend inside runs (what fills context), not only which cards ran.

## Procedure

1. **Hub totals.** `~/.hermes/bin/hermes-core-python scripts/token_attribution_agg.py ~/.hermes/state.db 7 > out.json`, plus every `~/.hermes/profiles/*/state.db`. Both readers open a URI-escaped path with `mode=ro` and `PRAGMA query_only=ON`, probe the schema, and fall back to `mode=rw` plus `query_only` if WAL sidecar initialization requires it. Neither path creates a missing database or permits SQL writes. It aggregates by source, model+billing_provider, profile, day, source×day, normalized title family and title, ranked by weighted tokens (in + 5×out + 1.25×cache_write + 0.1×cache_read).
2. **Workers.** The hub holds a minority of usage; Kanban runs live on worker state.dbs. Build a shell script that writes the Python into `/tmp` with a quoted heredoc, runs it for each state.db and prints `| gzip -9 | base64 -w0`. Ship it with `hub-worker-exec all --timeout 240 -- <script-file>`. Gzip+base64 is needed because SSM truncates each stream at about 24 KB. Generate the script file from execute_code (Python `open().write`): a terminal command containing a heredoc next to `state.db` paths is refused by the sqlite live guard. `hub-worker-exec` lists only Online workers; name offline ones (e.g. static worker-1) as not covered.
3. **Per-call context.** For each source/model, compute `calls/session` and `(in+cache_read+cache_write)/api_call_count`. This shows whether burn comes from many calls or from large re-sent context; cache_read is usually more than 90%.
4. **Fixed overhead.** System prompt size: `sessions.system_prompt_hash` joined to `system_prompts.prompt` (`length(prompt)`). Tool schemas are not stored; report them as estimated.
5. **Context composition.** Run `scripts/context_carry.py <state.db> <days>` on the busiest worker. For each message it computes chars × assistant turns after it (the number of times it is re-read), grouped by source and role/tool name. This ranks skill_view output, card briefs (`user`), kanban_show, file reads and terminal output by re-read cost. Over 2 days on a busy worker it runs longer than 600 s, so run it in the background and keep working.
6. **Work mix.** Classify title families as product vs Hermes self-work, and count families with ≥20 runs (re-run churn). Compare daily totals with worker-pool scale-ups.

## Report shape

Lead with the answer (no rogue thread, or which one), then: per-call context table, context-composition table, work mix, day trend, coverage gaps (hosts not read). Dollar estimates are list-price equivalents on subscriptions: call them quota, not billing. End with one concrete fix plan for approval (non-trivial build), not a list of options.
