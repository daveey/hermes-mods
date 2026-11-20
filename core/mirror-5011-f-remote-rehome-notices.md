# Remote rehome notices, moved-state claims and ask stripping

Mirror snapshot 5011; ordinals 4853, 4854, 4855, 4856, 4857, 4858, 4859, 4860, 4861, 4862, 4863, 4864, 4865, 4866, 4867, 4868, 4869, 4870.

Entries:

- **4853**: fix(kanban): refresh remote asks and dedupe moved notice states
- **4854**: fix(kanban): retain observation age and passive notice dedupe
- **4855**: fix(kanban): preserve unavailable-ledger fallback and trace contract
- **4856**: Fence moved notice claims and strip resolved ask presentations
- **4857**: Preserve refreshed guidance and defer contended passive claims
- **4858**: Recover a returning home after successor delivery fails
- **4859**: Fence legacy remote reoffers and strip embedded ask links
- **4860**: Preserve returning legacy recovery and unrelated notice context
- **4861**: Preserve remote notice recovery across transaction failures
- **4862**: Exercise legacy claim atomicity and replacement receipts
- **4863**: test(notices): assert transcript recovery without passive rewrites
- **4864**: fix(kanban): release partial remote claims after collection errors
- **4865**: test(kanban): cover passive custody and ask boundary guards
- **4866**: Refresh remote notice display before claim announcement
- **4867**: Assert preceding task links do not duplicate ask context
- **4868**: fix(kanban): preserve plain wake display metadata contract
- **4869**: Retire ended alias receipts after legacy skip commits
- **4870**: Exercise remote notice guidance and passive-open guards at their real boundaries

## Behavior

- **Asks and live state** (4853): a remote blocked notice refreshes a replica-only card; outcome metadata never relabels; a delivered terminal state is settled as moved without another turn; a dead holder's old-home transcript key is not reoffered; a new terminal state after a move still delivers once.
- **Observation age** (4854): moved observations keep the earliest late window; a live holder keeps custody after a home move; a delivered moved old state does not log undelivered.
- **Ledger fallback** (4855): unavailable-ledger fallback and the trace contract stay in place (no dedicated test in this range).
- **Claims and resolved asks** (4856–4857): moved notice claims are fenced; resolved real notices drop multiline ask presentations; a failed passive write releases the claim for retry; the sweep reserves custody before writing; opposite-home claims serialize; contended passive claims never fall back to an unclaimed write.
- **Returning home** (4858–4861, 4869): a returning home retries after a failed successor; ended-turn receipt retirement waits until the successor reservation commits; a mixed ended/live legacy skip still retires the ended receipt; rollback keeps receipts.
- **Legacy reoffers** (4859–4862): legacy returning homes respect a live successor; unconfirmed legacy delivery does not suppress a moved state; opposite-home legacy reoffers serialize.
- **Collection errors** (4864): per-row legacy alias reservations commit before later transcript reads; a collect-callback failure releases earlier unreturned claims; remote metadata does not infer a board from a malformed ref.
- **Display** (4866–4868): both notice projections refresh with the same bound before claim announcement; preceding task links keep intervening context once; a plain local wake refresh does not add a half display card.
- **Test-only** (4863, 4865, 4870): transcript recovery restores receipts without another write; ask removal preserves non-ask links; guidance does not strip a present or unrecognised card tail; a passive ledger open failure still writes the notice.

## How it works

- `tui_gateway/kanban_remote_notices.py` (`_claim_notice_locked`, `_without_superseded_ask`), `kanban_remote_identity.py`, `kanban_remote_observations.py`.
- `gateway/kanban_wake_fold.py`, `kanban_remote_owner.py`, `session_authority.py`, `session_claim_publication.py` for display refresh and claim announcement.

## Code

Shipped non-test paths (union over these entries):

- `gateway/kanban_remote_owner.py`
- `gateway/kanban_wake_fold.py`
- `gateway/session_authority.py`
- `gateway/session_claim_publication.py`
- `tui_gateway/kanban_remote_identity.py`
- `tui_gateway/kanban_remote_notices.py`
- `tui_gateway/kanban_remote_observations.py`

## Tests

Test files shipped by these entries (none were executed by this mirror pass; they are listed as shipped, and no pass result is claimed here):

- `tests/gateway/test_kanban_remote_notice_display.py`
- `tests/gateway/test_kanban_remote_owner_delivery.py`
- `tests/gateway/test_kanban_wake_fold.py`
- `tests/tui_gateway/test_kanban_remote_notices.py`
- `tests/tui_gateway/test_kanban_remote_rehome.py`

## Applying

These entries are part of the complete ordered snapshot 5011. Apply them only through the [Applying recipe in core/README.md](README.md#applying): the full ordered patch list with `git am -3 --empty=keep`, followed by the mandatory repair overlay that README names for snapshot 5011. They are not independent patches: later entries edit the files earlier entries create, and applying a subset is unsupported.

## Gotchas

- Home changes must not grant a second delivery of the same terminal state.
- Passive claim contention must not write without custody.
- 4855 has no new test in this range; treat its fallback as untested here.

## Plugin?

None.
