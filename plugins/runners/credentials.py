"""Credential-reference bridge for the management broker (stdlib only).

Management credentials live in a directory owned by the broker's own OS
identity (mode 0700, files 0600). The repair agent's Unix user cannot open
them; only the broker process resolves an opaque credential_ref to a value,
uses it for one outbound request, and never returns, logs or echoes it.
"""
import os
import re
import stat

REF = re.compile(r'cred_[0-9a-f]{16,64}')
MAX_BYTES = 8192


class CredentialError(Exception):
    """Raised without ever including credential material in the message."""


def _check_dir(directory, expected_uid):
    st = os.lstat(directory)
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise CredentialError('credential_dir_invalid')
    if st.st_uid not in (0, expected_uid):
        raise CredentialError('credential_dir_wrong_owner')
    if stat.S_IMODE(st.st_mode) & 0o077:
        raise CredentialError('credential_dir_not_private')


def load_secret(directory, ref, *, expected_uid):
    if not isinstance(ref, str) or not REF.fullmatch(ref):
        raise CredentialError('credential_ref_invalid')
    directory = os.fspath(directory)
    try:
        _check_dir(directory, expected_uid)
        fd = os.open(os.path.join(directory, ref), os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except CredentialError:
        raise
    except OSError:
        raise CredentialError('credential_unavailable') from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CredentialError('credential_not_regular')
        if st.st_uid != expected_uid:
            raise CredentialError('credential_wrong_owner')
        if stat.S_IMODE(st.st_mode) & 0o077:
            raise CredentialError('credential_not_private')
        if st.st_size > MAX_BYTES:
            raise CredentialError('credential_too_large')
        raw = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_BYTES:
        raise CredentialError('credential_too_large')
    try:
        value = raw.decode('utf-8').strip()
    except UnicodeDecodeError:
        raise CredentialError('credential_not_text') from None
    if not value or any(c.isspace() for c in value):
        raise CredentialError('credential_malformed')
    return value


def redact(text, secrets):
    out = str(text)
    for secret in secrets:
        if secret:
            out = out.replace(secret, '[REDACTED]')
    # Defense in depth for token shapes we did not load ourselves.
    return re.sub(r'\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})', '[REDACTED]', out)
