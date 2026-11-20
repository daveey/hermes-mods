# Fairness capture runtime (opt-in, default off)

Mirror entries 4996–5006 and 5009.

## Behavior

An opt-in recorder captures bounded urgency "read receipts" from kanban turn-urgency reads into durable evidence. Scheduling and scoring are unchanged.

- **4996** draft receipts: config (`kanban.fairness_capture.*`), a runtime `Session` with a bounded queue and writer thread, urgency receipt emit.
- **4997** retains draft capture context and health gates; **4998** isolates receipt failures so they never break reads, bounds payload preflight (oversize rejected before serializing, cycle/size guards, rejected payloads become durable loss), hot lookup never resolves HOME, close releases registration.
- **4999** fences runtime tails: receipts bound to sealed event bytes and deadline, in-flight reads tracked, detached payload snapshot, and a cross-process advisory-locked shared storage budget (`kanban_fairness_budget`, `kanban_fairness_tail`).
- **5000** authorizes runtime tails, fences rejected producers (a producer paused before loss cannot certify), one shared shutdown deadline, fork-inherited sessions ignored, batched drains; adds `docs/kanban-fairness-runtime.md`.
- **5001** explicit runtime startup binding (`kanban_fairness_activation`: `arm`, `disarm`, `start`, `boot`, `serving`) wired into dispatch daemon, gateway watchers, managed worker and the CLI.
- **5002** refreshes capture settings, `disarm` works after config reset or with config disabled (refuses another profile), workers hydrate the owner's capture without changing scheduler settings, bypass workers never load live capture.
- **5003** inventories runtime epochs and bounds root exports (`kanban_fairness_root`: inventory, register, bounded archive export).
- **5004** distinguishes revoked capture authority (authority loss cannot publish a clean tail; revocation during startup never yields an active epoch) and proves worker boot against real process shapes.
- **5005** real subprocess proofs (CLI arm writes a descriptor and refuses re-arm; cross-process disarm between authority read and activate; FIFO descriptors refused without blocking).
- **5006** user-guide configuration docs: `enabled` false by default and not itself arming; `capture_id` 1–128 chars; `directory` absolute or `<profile>/kanban/evidence/<id>`; `max_bytes` 1..536870912 (default 512 MiB), refusing writes at the bound rather than deleting evidence. Captures may remain incomplete.
- **5009** preserves trigger-loss evidence across sealing: pre-seal integrity is persisted atomically, observed drain gaps survive with no rows, observed loss is sticky after repair, a same-name no-op trigger is a gap, and legacy seals without receipts stay indeterminate.

## How it works

Default off: nothing starts without enabled config plus an explicit arm with identities. Producers call `submit` (non-blocking; 10,000 submissions never wait on the writer); the writer drains in batches into the journal/segments under the budget lock; seal requires clean observations and an unrevoked authority descriptor read at start and monitored thereafter.

## Code

`hermes_cli/kanban_fairness_{config,runtime,urgency,budget,tail,activation,root,journal,store,segments}.py`, `kanban_turn_urgency.py`, `config_defaults.py`, `kanban_db_dispatch.py`, `kanban_dispatch_daemon.py`, `kanban_ops.py`, `agent/managed_worker.py`, `gateway/kanban_watchers.py`, `gateway/session_managed_worker.py`, `scripts/kanban_fairness.py`, `docs/kanban-fairness-{runtime,capture}.md`, `website/docs/user-guide/configuration.md`.

## Tests

Source tests (not executed here): `test_kanban_fairness_{config,runtime,budget,shutdown,activation,activation_process,worker,gateway_process,process,root,journal,vertical}`, `test_kanban_urgency_receipts`. Source claims a 473-file consumer gate and a saturated 10000-read resource proof retaining explicit loss.

## Applying

Never apply independently. Follow the full segmented recipe in the core mods [README](README.md) ("Applying"); depends on [roster/envelope work](mirror-5011-g-fairness-roster-and-evidence-envelopes.md).

## Gotchas

- Not a fairness certification; do not enable fleet-wide without a separate approved plan.
- Profile isolation matters: a held dispatch boots the process home, not an inherited profile.
- The budget refuses writes rather than evicting evidence.
