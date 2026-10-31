# SSH-served Desktop uses the host owner's identity

Patch: 1339 (`11fe8584050`).

## Behavior

An authenticated SSH-served Desktop is recognized as the host owner when
checking access to that owner's Desktop chat history. Previously its
`desktop-ssh` credential stamp was treated as a different owner.
Both legacy and versioned SSH Desktop stamps compare equal to the host owner,
not to an unrelated OS user.

## How it works

`desktop-ssh` joins the bundled login providers in `_BUNDLED_LOGINS`.
The existing owner-identity mapping therefore handles the validated SSH token's
ownership ID like the other bundled login credentials. This changes identity
normalization, not token validation or the SSH bootstrap protocol.

## Code

- `gateway/owner_identity.py`: bundled login provider set.

## Tests

In `tests/gateway/test_webapp_owner_identity.py`:
- `test_another_macs_ssh_desktop_edits_the_owners_desktop_chat`: builds the
  REST mutation actor and checks `authorize_history` for an owner-held chat.
- `test_a_stamp_an_ssh_desktop_stored_reads_as_the_owners`: legacy and
  `auth:v1` stamps, including the unrelated-user negative control.

These tests were inspected, not executed for this documentation task.

## Applying

Apply 1339 in mirror series order. No new configuration or plugin is required.

## Gotchas

The regression exercises the shared authorization seam, not actual archive,
pin, or rename HTTP endpoints or a live SSH Desktop connection; those are untested here.
