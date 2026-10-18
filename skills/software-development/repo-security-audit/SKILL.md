---
name: repo-security-audit
description: "Use when auditing a repo for security flaws and fixing them."
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [security, audit, remediation, rotation]
    related_skills: [subagent-driven-development, token-broker, babybear-development]
---

# Repo security audit + remediation

## When to Use

The user asks to audit, review, or threat-model a repo or deployed system for security issues — and especially when they also want the findings fixed: fix sessions, history scrubbing, credential rotation, data-store purge. Per-repo architectural facts live in that repo's own skill; this one carries the cross-repo procedure.

## Procedure

1. **Orient on fresh clones, not working checkouts.** Clone each repo shallow (`--depth 100`) into a scratch dir. Working checkouts are often dirty/stale on side branches; history greps against them hit unrelated worktrees. Record the audited commit in the report so findings are attributable.
2. **Dispatch one audit subagent per repo** with a threat model enumerating the actor positions (anonymous internet, LAN peer, stolen credential, supply chain, prompt-injection→agent-with-tools) and demand file:line-grounded findings with concrete attack scenarios — 5 grounded findings beat 20 speculative ones, and "verify the signed-command implementation actually works" is as valuable as finding holes.
3. **Live-verify the top findings yourself before acting on them.** Probe prod read-only first (does the leaked credential still authenticate? does the unauth request actually return 200?). An audit claim of "rotated" or "dead key" is a hypothesis until tested — AWS STS `get-caller-identity` with the candidate creds, curl the endpoint with and without auth. Never print secret values in transcripts or terminal output; pipe them between processes.
4. **Group findings into non-overlapping work areas** and dispatch one fix session per group in an isolated worktree (see subagent-driven-development for the parallel-worktree pattern): auth core, web layer, upload/ingest pipeline, availability, secrets/history, CI pins. Name a scope guard in every brief — the exact functions/files sibling sessions own. All sessions branch-only; the controller integrates.
5. **Integrate sequentially, resolve conflicts once, then verify**: run the combined security-test battery plus core suites on the merged tree; reproduce any remaining failures on pristine main before accepting them as pre-existing. Fix fixtures broken by the merge itself (a security fix that makes auth mandatory breaks every test fixture that boots without credentials — update fixtures to the new contract, don't weaken the fix).
6. **Rotate in dependency order**: password/tokens first (generate in-process, never on disk in the repo), then sync every downstream copy (Worker store, Fly secrets, GitHub secrets, broker scopes), then purge git history (filter-repo on a sacrificial clone, force-push, delete stale leak-carrying branches), then purge data stores (D1 rows, FTS, R2 objects, snapshots). Verify each rotation live: old credential → 401, new → 200, anonymous → 401.
7. **Store rotated credentials in the token broker** as `user/<name>` auto scopes (see token-broker skill) so future sessions can vend them — not in repo files or chat.

## Pitfalls

- Grep history for the leaked string AFTER the fix sessions land, not just the audit — a fix session may re-introduce it as a test fixture value ("wrong password" literals). Scrub the fix first, then rewrite history, or the rewrite re-imports the leak.
- Test suites that boot servers without credentials fail wholesale once auth becomes fail-closed; check whether the module caches env at import (`AUTH = os.environ.get(...)` at module level) — fixtures must set the module attribute or boot a subprocess with env, and raw-socket probes must carry the auth header.
- Data-store purge must follow the data's actual replication paths, not the obvious one: an indexed note lands in the FTS index and in hashed R2 objects and in daily/monthly snapshots with different key schemes — compute the hash keys from the ingest code, and remember monthly snapshot copies have no lifecycle rule.
- Don't trust a commit message claiming rotation; test the old credential against the live service.
- Force-push rewrites fire auto-deploy; verify prod health (and the security fix itself) right after the push, not just the git state.
