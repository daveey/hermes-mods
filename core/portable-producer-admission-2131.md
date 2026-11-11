# Portable producers and non-nice CPU admission

Mirror ordinals: **2115, 2116, 2123**.

## Behavior

With worker pools configured, eligible portable light/heavy cards created by direct database producers enroll in `worker-first-v1`, matching tool and CLI creation. Worker children and cards linked to a chat origin can enroll: chat storage/ownership does not itself pin execution. Courier creation retains its immutable placement envelope and does not gain automatic enrollment.

Conservative exclusions remain: nondefault board, nonportable workspace/project/skill/assignee or host markers, gateway requirements, explicit placement and `runs_on` (including an empty list). The effective board is respected even when the caller omits it. Host quota-storm recovery stays local because the producer explicitly describes work on `this host`.

The CPU overflow admission sample excludes positive-nice user CPU as well as idle and iowait. System, IRQ and steal remain busy. This changes CPU accounting, not memory, slot, drain or freshness guards.

## How it works

`create_task` resolves inherited project/workspace state before `resolved_db_placement` validates it. Unchecked direct producers use the shared creation decision. Front doors set the internal `placement_checked` flag to preserve their already-resolved enrollment or deliberate non-enrollment. Courier creation strips that internal flag from the immutable envelope and passes a checked decision to insertion.

Direct DB audit fields are normalized from the actual connection's board and effective project. `created` event payloads carry authoritative portability information. Tool creation similarly audits normalized assignee, skills and inherited project rather than raw optional arguments. Ordinal 2123 changes audit inputs, not the enrollment policy itself.

`machine_cpu_metrics.parse_proc_stat` returns total and admission-excluded time with nice included in the latter. Busy is `100 * (delta_total - delta_idle - delta_iowait - delta_nice) / delta_total`. All positive-nice CPU is excluded, not merely processes with nice at least ten. Invalid readings remain unknown/refused, not idle.

## Code

- `hermes_cli/kanban_worker_placement.py`, `kanban_db.py`: insertion-time placement, normalization and audit.
- `hermes_cli/kanban.py`, `kanban_origin_creation.py`, `tools/kanban_tools.py`, `tools/kanban_tools_placement.py`: checked front doors, origin distinction, effective board/audit fields.
- `hermes_cli/kanban_placement.py`: `journalctl` and `this host` exclusions.
- `hermes_cli/kanban_provider_walls.py`: explicit local recovery wording.
- `hermes_cli/machine_cpu_metrics.py`: non-nice accounting; `docs/worker-first-v1.md` and configuration docs describe it.

## Tests

Source regression tests were inspected, **not executed here**.

- `tests/hermes_cli/test_kanban_worker_policy_db.py`: direct producers, first-transaction policy/event fields, exclusions, connection-derived board, immutable courier retry/envelope, authoritative direct-producer audit.
- `test_kanban_placement.py`: tool origin-bound portable cards, worker children, explicit auto/default pin behavior and CLI/tool/origin-tool current nondefault board exclusion. `test_kanban_worker_placement.py` adds marker exclusions.
- `test_kanban_provider_walls.py`: a quota-storm producer opens one local card with a pool enabled.
- `test_machine_cpu_metrics.py`: nice is excluded, and non-nice load still rejects admission.
- `tests/tools/test_kanban_creation_audit.py`: effective creation fields preserve enrollment, including implicit assignee and inherited project cases.

These cover the named source entry paths; they do not prove a live worker lease or remote execution occurred.

## Applying

Replay the complete snapshot through [README.md](README.md), not this subset as standalone patches. Existing worker-pool configuration is still required; this does not create a pool or rewrite existing cards. CPU gating uses installation-root `kanban.admission.cpu_busy_below_pct`; profile overrides do not activate it. Run the listed suites in a disposable source environment before rollout.

## Gotchas

- `placement_checked` is internal provenance of a front-door decision, not a user option to bypass validation.
- A chat origin and a courier owner envelope are different contracts.
- Accurate audit text does not authorize a placement change; 2123 intentionally keeps enrollment unchanged.
- Lower measured non-nice busy does not establish available memory or I/O capacity.
- Existing worker-first lease/fallback fences remain necessary after enrollment.

## Could this be a plugin?

A plugin can produce cards or choose policy inputs. Uniform first-transaction enrollment and authoritative audits belong in the shared database/front-door core, otherwise direct producers bypass them. Admission CPU accounting likewise belongs beside the metrics consumed by the core gate.
