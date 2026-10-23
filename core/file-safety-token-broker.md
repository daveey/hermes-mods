# File-safety denies the token-broker client token

Patches:

- `patches/0217-fix-file-safety-read-write-deny-.config-token-broker.patch`: `~/.config/token-broker` joins the credential dirs and a new read-deny list.

What you see: the agent can no longer read, search, attach or overwrite the enrolled token-broker client token (`token`, `token.expired-*`).

## Behavior

- `.config/token-broker` is in `HOME_CREDENTIAL_DIRS`: write-denied, excluded from profile export, refused as an `@file` reference.
- New `HOME_READ_DENIED_DIRS` makes `read_file`, `search_files`, `vision_analyze` and `raise_if_read_blocked` refuse it, under every guard home.

## Why

The client token lets its holder exec auto-tier broker scopes with no approval, so reading it is equivalent to holding those secrets.

## Code

`agent/file_safety.py`.

## Tests

`tests/agent/test_file_safety_token_broker.py` (read and write refusal for the token and rotated copies, across guard homes).
