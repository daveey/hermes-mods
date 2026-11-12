# Agent clients trust an exported SSL_CERT_FILE on macOS

Patches: **2397–2398**.

## Behavior

When a certificate environment variable is set and no explicit verification override or provider CA bundle takes precedence, `resolve_httpx_verify()` returns the shared `platform_ssl_context()`: an existing `SSL_CERT_FILE` bundle is trusted in addition to the platform store, a stale path falls back to the platform store, and `SSL_CERT_DIR` alone stays platform-only. Previously macOS Security.framework ignored `SSL_CERT_FILE`, so clients relying on an environment-only private CA could reject its certificates.

## How it works

One policy implementation (and one cached context) is shared by both resolvers, removing a duplicated copy that diverged.

## Code

`agent/ssl_verify.py`.

## Tests

Source coverage, **not run in this mirror** (the mirror pass only verifies patch replay and tree equality; it does not rerun these tests): `tests/agent/test_ssl_verify.py` (stale path fallback, identity contract), `tests/agent/test_ssl_transport_contracts.py` (loopback private-CA handshake via `SSL_CERT_FILE`; 2398 closes the shared pool afterward).

## Applying

Use the [README full replay procedure](README.md#applying). These patches are not independent of earlier core patches; apply the whole series in order.

## Gotchas

The patch notes say CI is Linux-only where OpenSSL masks the bug; this mirror did not run it on macOS.

## Plugin

Core change; no plugin required.
