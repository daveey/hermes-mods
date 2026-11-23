# Scope-renewal evidence from a read-only shadow

Patch coverage: **5844**.

## Behavior

An owner chat can submit a scope renewal for a foreign-home card visible only in the read-only shadow. The local primary remains free of that foreign card, and the canonical service still decides whether renewal is authorized.

## How it works

The CLI opens the target through `kanban_shadow_view.open_card` using an `ExitStack`. Actor resolution, mandate preparation and comment-body lookup use that evidence connection. The canonical submission continues using the primary board identity. A returned canonical event is applied locally only when evidence is the primary connection; shadow-only evidence waits for normal sync delivery to its home.

## Code

`hermes_cli/kanban_scope_renewal_cli.py`: `renew`, `_renew` and `_canonical` separate evidence reads from write ownership.

## Tests

Source regression coverage listed below was inspected, **not executed in this mirror pass**. No live deployment or performance result is claimed.

`tests/hermes_cli/test_kanban_scope_renewal_shadow.py` covers owner-chat submission through the CLI, exact mandate comment/episode coordinates, unchanged shadow file hash and primary change count, pre-submission refusals, spoofed process ancestry and canonical refusal after stale shadow authority.

## Applying

Apply the covered patches in the full ordered core series using the [core applying recipe](README.md#applying), including its replay bridges and required repair overlay. These entries depend on earlier core changes; they are not advertised as an independent cherry-pick set. Reimplementation should preserve the invariants above and run the named source suites in a disposable source checkout.

## Gotchas

Read access to a shadow is not authority. A locally valid-looking mandate may become stale before canonical revalidation. This change does not make renewal a status promotion or grant the shadow a writer connection.

## plugin?

No: CLI actor resolution and canonical renewal submission are core custody boundaries.
