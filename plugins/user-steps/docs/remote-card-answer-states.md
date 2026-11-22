# Remote card-answer states

Scope: a Desktop decision recorded against a card owned by another gateway (`plugins/user-steps/remote_cards.py`,
core `hermes_cli/kanban_card_answer*`). Core is frozen; everything here is enforced in this plugin and its
ActionStore.

## Parties and custody

| Party | Holds |
|---|---|
| Source ActionStore (this plugin) | The recorded operation and its dispatch state. Its `cancelled` state is the only thing the core source-proof reader checks: a cancelled operation gets no new proof. |
| Courier (hub-sync) | The envelope, from queue to POST (`sent.state`: local, queued, sent, accepted, rejected). |
| Owner gateway | The only authority on whether the answer committed. Its receipt is the single success signal. |

The source proof read and the owner commit are separate steps in separate databases, so no source-side mark can
make them atomic. The design therefore never treats our own cancellation as proof that the owner did not commit.
`cancelled` + `retired.evidence == "remote-retiring"` means only "stop issuing proofs and stop retrying".

## States of one remote operation

```
pending ──valid receipt (read twice)──────────────────────────► delivered   (terminal)
pending ──authenticated refusal rejected/expired/failed───────► cancelled   (terminal, remint allowed)
pending ──departure / generation change, no receipt, no refusal► retiring    (cancelled + marker; held)
retiring ──valid receipt, at any later time (read twice)──────► delivered   (terminal; late success)
retiring ──authenticated refusal───────────────────────────────► cancelled   (terminal, remint allowed)
retiring ──envelope gone/rejected AND proof refusal ≥ 120 s ──► cancelled   (terminal, remint allowed)
retiring ──anything else (404, in flight, offline, sent)──────► retiring    (held)
```

Rules:

1. `retiring` is not terminal. A receipt that turns up later always converts it to `delivered`
   (`ActionStore.settle_late_receipt`); the request resolves exactly as an ordinary delivery would.
2. A remint (new tile for the same target and a fresh generation) needs a terminal fact: an authenticated owner
   refusal, or the local courier envelope absent/`rejected` together with a proof refusal that has been in force for at
   least `STABLE_REFUSAL_S`. A 404, absence, an in-flight/`sent`/`queued`/`accepted` envelope or an unreadable
   observation never counts.
3. Offline or unverifiable observation does not enter `retiring`; the operation stays `pending` and keeps retrying.
4. Nothing is observed or retired while a dispatch of the operation holds the delivery lease.
5. An undecided open tile whose generation changed is withdrawn. If a click committed at that boundary the
   withdrawal is refused and the target is held: the replacement tile is absent, so exactly one operation exists.
6. A held target is never reminted and never offers another action.

## Presentation

The `delivery` field on a presented request exists only in `retiring`:

* `Delivering…` while younger than `UNCONFIRMED_AFTER_S` (the courier retry window).
* `Delivery to <gateway> not confirmed (<n> min)` afterwards, with `unconfirmed: true`. Still held, still no new
  action, no remint.

The panel never shows "Cancelled" for a retiring operation.

## Tests

`tests/test_remote_card_state_machine.py` has one test per transition above;
`tests/test_remote_card_async_custody.py` covers the reviewer's async `_send_now`/`send_queued` interleaving against
real core + Courier (expected: delivered, exactly one comment) and the cancel-before-proof control;
`tests/test_remote_card_click_race.py` covers rule 5.
