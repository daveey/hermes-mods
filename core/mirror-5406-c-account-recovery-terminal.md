# Gateway-bound selected-account recovery terminal

Mirror entries: 5240. Related implementation and regression commits are grouped here; later fixes in the group supersede their intermediate behavior.

## Behavior

Desktop exposes a constrained recovery PTY for an allowed provider and stored credential row on the gateway that owns that row. The renderer supplies identifiers, not executable text. The only current provider allowlist entry is `anthropic`; the fixed command is `hermes [--profile <name>] auth reauth <provider> <row>`.

Invalid or unsupported targets start nothing. A missing gateway, no-shell transport, unavailable runtime, Windows target or unknown SSH launcher never falls back to signing in on this device. Recovery terminal input, resize, disposal and output are scoped to the initiating renderer.

## How it works

IPC validates row/profile alphabets and length before constructing arguments. Local resolution reuses the app backend launcher without triggering a first-run installation and refuses shell-shim launchers. SSH recovery uses the owning gateway's known launcher and optional home, shell-quotes each argument and uses `exec` so the PTY ends with the recovery process. Session tracking retains buffered output/exit handling and releases the PTY master on child exit. Recovery cwd lookup returns no shell cwd.

## Code

Paths below are relative to the source Hermes checkout, not files supplied by this documentation:

- `apps/desktop/electron/external-terminal.ts`
- `apps/desktop/electron/main.ts`
- `apps/desktop/electron/preload.ts`
- `apps/desktop/electron/terminal-ipc.ts`
- `apps/desktop/electron/terminal-recovery.ts`
- `apps/desktop/src/global.d.ts`
- `website/docs/user-guide/selected-account-recovery.md`

## Tests

**Source test coverage only — not executed by this mirror documentation task.**

Electron source tests validate payload grammar and fixed argv, quote the remote command, exercise local and SSH IPC launch selection, verify no-process refusals across invalid/missing/no-shell/Windows/no-runtime/unknown-path cases, enforce sender ownership for control and output, and retain the ordinary terminal write path. The PTY/SSH/runtime boundaries are mocked; real interactive provider sign-in is untested.

Relevant source suites/fixtures:

- `apps/desktop/electron/terminal-recovery.test.ts`

All runtime entry paths not explicitly covered above are **untested** by the cited batch coverage; no live deployment or provider run is claimed.

## Applying

Apply only as part of the **full ordered 5406-series** using the snapshot's [README Applying recipe](README.md), including its pinned replay bridges and **mandatory post-replay hostname-escape repair**. Follow the snapshot inventory and repaired-tree verification; do not cherry-pick this document's ordinals as a certified independent patch set. This document records source behavior/coverage, not independent patch certification or an executed mirror acceptance result.

## Gotchas

This entry supplies the transport/IPC primitive, not proof of a complete account-recovery UI or a successful sign-in. It is not an arbitrary command launcher. Unsupported transports must be surfaced rather than silently redirected.

## Why core instead of a plugin

Electron main/preload IPC and PTY renderer ownership are privileged core boundaries. A renderer plugin may call the constrained API but must not implement its own unchecked process launch.
