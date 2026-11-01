# Tailnet

`tailnet_hosts`, `tailnet_exec` and `tailnet_copy` use the token broker to run SSH/SCP from the gateway. Inventory, session approval and staging configuration are described in `__init__.py`. Credentials enter only the broker-launched runner, which removes the key from its environment, writes a private temporary key file when execution is permitted, and deletes it afterward.

## Machine-wide workload policy

The runner checks **before creating a key directory/file or starting SSH/SCP**. This covers both plugin tools (including approved requests) and direct `token-broker exec -- python runner.py` calls with jobs on stdin. Workload refusals return JSON with `rc: 126`, `ran: false`, `blocked: true`, and an explanation. Approval does not override workload placement.

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
- Unbounded transfer/archive commands: `rsync`, `scp`, `cp`, `ditto`, `tar`, `bsdtar`, `unzip`, `dd`, `curl`, `wget`, `rclone`, `sftp`, and `install`. These conservatively redirect to bounded `tailnet_copy`; even a purported small-file command or `rsync --max-size` is refused, since it does not bound aggregate work.

Ordinary read/cleanup commands remain available, including `hostname`, `df`, `du`, `rm`, `xcodebuild -version/-list/-show*`, `xcodebuild clean`, and `simctl list/delete/shutdown`. Targeted pytest files and `docker ps` remain available. Heavy actions in a compound command still cause refusal; `bash -lc`, `env`, and common wrappers are inspected. Malformed quoted shell payloads fail closed.

### Copies

On protected targets, `put` must stat as a regular file, be within the caller's `max_bytes`, and be **strictly below 1 GiB** regardless of an increased caller limit. The plugin's existing 25 MiB staging limit is unchanged.

**All protected `get` jobs are refused before SSH**, even with a small requested limit: this runner has no safe pre-transfer remote-size enforcement. Checking the local file after SCP is not a workload bound. Route the transfer elsewhere rather than raising the limit. Unprotected/absent-policy copy behavior remains unchanged, including its legacy post-download size check.

## Boundaries

This is a workload-placement guard, not a shell sandbox or a resource quota. It recognizes command syntax, not arbitrary program behavior. Remote scripts, user-defined functions/aliases, dynamic evaluation, custom binaries and interpreter programs can perform work that static command matching cannot see. Upload size checks are local preflight checks, not immutable-file or streaming quotas; callers must not mutate staged files during transfer. The policy, runner source and process home/environment must be administrator-controlled. Live DNS/SSH/macOS execution is not exercised by the tests.

## Tests

```sh
uv run --with pytest --with pyyaml python -m pytest -q -p no:cacheprovider plugins/tailnet/tests_tailnet.py
```

`tests_tailnet.py` drives the real runner subprocess through a fake broker and fake SSH/SCP. DNS is replaced in child processes; no target network is contacted. Tests assert that refusals create no key directory and invoke no SSH, cover both direct and plugin entry paths, policy failures, address aliases/resolution, classifier failure, wrappers/opt-outs, safe commands and unprotected CI, plus sparse-file upload cap boundaries and protected download refusal.
