# denial-substitution-guard — bounded spec

## Observed failure (recorded shapes; real ids are in private evidence, fixtures here are synthetic)
1. Call A: `token-broker exec --scope aws.write.hermes … -- aws ssm send-command --instance-ids <I>` returned
   `AccessDeniedException … User: arn:aws:sts::<acct>:assumed-role/token-broker-agent-write/tbk-… is not authorized to
   perform: ssm:SendCommand on resource: arn:aws:ec2:<region>:<acct>:instance/<I> with an exp …(+73 chars)`.
   The transcript line is TRUNCATED mid-sentence. "explicit deny" is NOT established by the recorded text: the guard
   records `explicit=None` (unknown) unless the full qualifier is present.
2. ~16 min later a different delegate ran `aws ssm send-command --instance-ids <I>` with no broker wrapper: the
   ambient instance-profile role (`assumed-role/hermes-hub/i-…`), which succeeded and ran remote commands.
   No intervening grant was verified. Same operation, same resource, different principal.

## Rule (one)
After an AccessDenied for (action A, resource R) under invocation identity X, a later tool call that performs A on R
under a different identity (broker scope / profile / ambient) or through a different client (aws CLI, boto3, nested
terminal in execute_code, tool_call, tailnet_exec) is BLOCKED unless an independently verified grant for (A, R) dated
after the denial exists.

## Not in scope / must stay allowed
- other actions on R (e.g. `ssm:GetCommandInvocation`, `ec2:DescribeInstances`), same action on other resources;
- same identity retrying (not a substitution);
- denials older than TTL (24 h);
- `run_in_user_terminal`, `desktop`, human-run commands (supported owner/human boundary is never classified);
- no broad ban, no IAM/config/credential edits, no live AWS probes, no auto-ban.

## Design
- `post_tool_call`: parse the result for `User: <principal> is not authorized to perform: <svc:Action> on resource: <arn>`;
  append `{at, action, resource, principal, identity, explicit, truncated}` to `$HERMES_HOME/denial-ledger.jsonl`
  (0600). `identity` comes from the *invoking text* (`token-broker exec --scope S` → `broker:S`; `--profile`/`AWS_PROFILE` →
  `profile:P`; else `ambient`). Host-wide file, so it crosses delegate/subagent sessions (the incident did).
- `pre_tool_call`: parse the call text (shell tools, execute_code literals + boto3 method calls, nested `tool_call`) to
  `(action, resource tokens, identity)`; match ledger by action (kebab/snake/Camel normalised) and resource id as a whole token.
- Grants: `$HERMES_HOME/denial-grants.json` `[{action, resource, granted_at, source, verified_by in {owner, broker}}]`;
  only entries after the denial count. The guard refuses shell/Python text that writes the ledger or grants file
  (agent self-grant); reading is fine.
- Separate plugin from the root-cap guard: no shared files.

## Known limits (reported, not covered)
- A call whose action is only inside a script file (`python3 x.py`) is recorded when it is the DENIED call (identity from
  wrapper) but a later script-file replay is not detected.
- Ledger is per host; denial on hub does not bind a worker host.
- The grants file is agent-writable at the OS level; only tool-text tamper is refused.
- Raw HTTP SigV4 clients (curl/requests) are not parsed.
