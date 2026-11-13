# Tailnet

`tailnet_hosts`, `tailnet_exec` and `tailnet_copy` use the token broker to run SSH/SCP from the gateway. Inventory, session approval and staging configuration are described in `__init__.py`. Credentials enter only the broker-launched runner, which removes the key from its environment, writes a private temporary key file when execution is permitted, and deletes it afterward.

## Approval and `auto_hosts`

A profile's local turns (CLI, cron, Kanban workers; never a chat user) skip the owner-approval DM only on hosts listed in that profile's own `tailnet.auto_hosts`; every other host opens a grant-once request per command. The recommended fleet default is **every host in your inventory**, set in every profile and on every worker that runs local tailnet turns, so unattended work never waits on an approval DM. Add a new inventory host to every `auto_hosts` list in the same change. Chat turns from anyone but the owner keep the approval gate regardless of `auto_hosts`, and the workload policy below still applies.

## Machine-wide workload policy

The runner checks workload syntax **before creating a key directory/file or starting SSH/SCP**. This covers both plugin tools (including approved requests) and direct `token-broker exec -- python runner.py` calls with jobs on stdin. These refusals return JSON with `rc: 126`, `ran: false`, `blocked: true`, and an explanation. Protected downloads additionally perform authenticated metadata preflight before SCP; failed metadata returns a nonzero rc without transferring the file. Approval does not override workload placement.

The runner always passes `--` before `user@addr` to ssh/scp (and to the jump host's ssh) and refuses a target whose user or address is not a plain value (a leading `-`, spaces or shell metacharacters) before SSH starts. When the job failed before SSH/SCP ran, the reply keeps `rc: 255` for older callers but adds `failure`: `"no-key"` (the broker vended no key) or `"local"` (ssh missing, a refused target, a local exception). A reply with `rc: 255` and no `failure` is SSH's own exit: it could not connect or authenticate.

An administrator may install this private JSON file at **`Path.home() / ".hermes/tailnet/workload-policy.json"`**. It is intentionally not relative to `HERMES_HOME`, a profile, or a job-supplied path. Keep real machine identities out of this public repository.

```json
{
  "version": 1,
  "protected_addresses": ["192.0.2.10"],
  "protected_aliases": ["laptop.example"],
  "routing_message": "Route portable work to CI; macOS work to a dedicated build Mac."
}
```

All four fields are required; unknown fields and unsupported versions are rejected. Addresses must be IP literals; aliases are address spellings/DNS names, **not inventory display names**. Include each protected machine's IP addresses as well as its aliases. Arrays may be empty. The routing message must be nonempty.

- No policy file: backward-compatible behavior, with no private targets hardcoded.
- Present but malformed/unreadable policy: refuse the job, even light commands.
- With policy configured: normalize case/trailing dots/IP spelling (including IPv4-mapped IPv6), match aliases and addresses, and inspect DNS answers. Any answer matching a protected IP makes the target protected. Unresolvable targets are refused.
- Permitted jobs use the inspected numeric address, not a second DNS lookup. The jump host is also resolved locally. SSH/SCP use `-F /dev/null` while policy is active, so local SSH configuration cannot silently redirect the inspected destination. This means SSH-config-only aliases and jump-only DNS names need explicit, locally resolvable inventory addresses.
- No job fields, `MAC_HEAVY_OK`, or `MAC_HEAVY_GUARD` disable this check. Profile `auto_hosts` controls approval only.

## Protected targets

The runner imports sibling `mac-heavy-guard/guard.py` by file path and uses `commands`/`check_argv` for whole-suite, container-build, release-build and whole-repository-install patterns. It does **not** call `check_shell`, honor its opt-outs, or follow gateway-local script files as though they were remote scripts. Missing/broken classifier code fails closed for protected exec jobs. The sibling plugin's behavior is unchanged.

Additional tailnet restrictions:

- `xcodebuild build`, `test`, `build-for-testing`, `test-without-building`, `archive`, and implicit/default builds, including through `xcrun`.
- `xcrun simctl boot`, `create`, `install`, and `bootstatus`.
- `swift build/test` and `cargo build/test` (including debug builds and Cargo `b`/`t`).
- Unbounded transfer/archive commands: `rsync`, `scp`, recursive `cp` (`-r`, `-R`, `-a`, `--recursive`, `--archive`) or `cp` with glob/brace operands, `ditto`, `tar`, `bsdtar`, `unzip`, `dd`, `curl`, `wget`, `rclone`, `sftp`, and `install`. These conservatively redirect to bounded `tailnet_copy`; even a purported small-file command or `rsync --max-size` is refused, since it does not bound aggregate work. A non-recursive `cp` of named files (`cp ~/.ssh/authorized_keys ~/.ssh/authorized_keys.bak`) is bounded and allowed, so a backup before a small edit has a sanctioned route.

## Terminal path (`ssh`/`scp`/`sftp`/`rsync`)

The policy is per destination, not per tool: the plugin also registers a `pre_tool_call` hook (`terminal_guard.py`) for the `terminal` tool (and `terminal` calls batched through `tool_call`). It is registered even when `tailnet.enabled` is false, and does nothing without the policy file. For every `ssh`, `scp`, `sftp` or `rsync` in the command (compound commands, `bash -c` payloads, wrappers such as `env`/`timeout`/`nice`, command substitutions and heredocs included):

- The destination is protected when its host part (after `user@`, or in an `ssh://` URL), or the `hostname` that `ssh -G <the command's own options> <dest>` prints (3 s timeout; so SSH-config aliases and `-o HostName=` count), is a protected alias or address, or resolves to a protected address. A name that does not resolve here is treated as unprotected.
- `ssh` to a protected machine: the remote command goes through the same classifier as `tailnet_exec`. A heredoc body read by the remote login shell or by `bash -s`/`sh` is classified with it. An interactive session (no command), a remote shell reading a script from unknown stdin (`cat x | ssh host bash -s`, `ssh host < x`), an `-s` subsystem, or text that cannot be lexed is refused (fail closed). Forwarding-only (`-N`, `-W`), `-G` and `-V` are allowed.
- `scp`/`sftp`/`rsync` with a protected end: refused; use bounded `tailnet_copy`.
- Refusals read `tailnet workload policy: <reason>; <routing_message>`, the runner's wording. A malformed policy refuses any command that names one of these tools.

`plugins/mac-heavy-guard` still skips `ssh` (it guards the local machine); this hook is the remote half.

Ordinary read/cleanup commands remain available, including `hostname`, `df`, `du`, `rm`, `xcodebuild -version/-list/-show*`, `xcodebuild clean`, and `simctl list/delete/shutdown`. Targeted pytest files and `docker ps` remain available. Heavy actions in a compound command still cause refusal; `bash -lc`, `env`, and common wrappers are inspected. Malformed quoted shell payloads fail closed.

### Copies

On protected targets, `put` must stat as a regular file, be within the caller's `max_bytes`, and be **strictly below 1 GiB** regardless of an increased caller limit. The plugin's existing 25 MiB staging limit is unchanged.

Protected `get` jobs require a literal absolute path (letters, digits, underscore, dot, slash and hyphen only; no `.` or `..` components) and a positive integer bound no larger than 25 MiB. The public tool supplies this bound internally; no extra argument is needed. The same pinned, isolated SSH transport checks that the source is a regular file, not a final symlink, then reads its size with BSD stat or GNU stat. Failed, malformed or oversized metadata refuses the copy. SCP inherits an OS file-size limit, so growth after stat cannot write past the bound; failed transfers discard partial output. Unprotected/absent-policy copy behavior remains unchanged, including its legacy post-download size check.

## Boundaries

This is a workload-placement guard, not a shell sandbox or a resource quota. It recognizes command syntax, not arbitrary program behavior. Remote scripts, user-defined functions/aliases, dynamic evaluation, custom binaries and interpreter programs can perform work that static command matching cannot see. Upload size checks are local preflight checks, not immutable-file or streaming quotas; callers must not mutate staged files during transfer. The policy, runner source and process home/environment must be administrator-controlled. Live DNS/SSH/macOS execution is not exercised by the tests.

## Tests

```sh
uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider plugins/tailnet/tests_tailnet.py plugins/tailnet/tests_terminal_guard.py
```

`tests_terminal_guard.py` calls the hook in-process with a fake `ssh` that only answers `-G` and in-process fake DNS; it asserts the guard never starts a real ssh.

`tests_tailnet.py` drives the real runner subprocess through a fake broker and fake SSH/SCP. DNS is replaced in child processes; no target network is contacted. Tests assert that syntax refusals create no key directory and invoke no SSH, cover both direct and plugin entry paths, policy failures, address aliases/resolution, classifier failure, wrappers/opt-outs, safe commands and unprotected CI, plus sparse-file upload cap boundaries. Download tests cover a small protected copy, failed/malformed/oversized metadata, unsafe paths, unbounded limits and growth after stat. The generated stat command also runs through a real local shell against regular files, directories, FIFOs, symlinks and missing paths.
