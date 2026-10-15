# Dashboard trusts a Cloudflare Access login (opt-in)

Patches:

- `patches/0037-feat-dashboard-auth-opt-in-trust-of-Cloudflare-Acces.patch`: dashboard auth. When configured, a
  verified Cloudflare Access login on an allowlisted email counts as a dashboard session.

What you see: put the dashboard behind a Cloudflare Access application, set three environment variables, and you log
in once, at Access. Stock Hermes asked for its own provider login on top. `/login` sends you straight back to the
page you came from. With the variables unset, nothing changes.

## Behavior

- **Off unless all three settings are present** (per host, in the environment):
  - `HERMES_DASHBOARD_CF_ACCESS_TEAM_DOMAIN`: the Access team domain, e.g. `myteam.cloudflareaccess.com`. A leading
    `https://` and a trailing `/` are stripped; it is lowercased.
  - `HERMES_DASHBOARD_CF_ACCESS_AUD`: comma-separated Access application AUD tag(s).
  - `HERMES_DASHBOARD_CF_ACCESS_EMAILS`: comma-separated allowed emails, case-insensitive.
- **A request is logged in** when its `Cf-Access-Jwt-Assertion` header is an RS256 JWT signed by the team's certs,
  with `iss` equal to `https://<team domain>`, `aud` matching one of the configured tags, `exp`/`iat`/`sub` present,
  and an `email` claim on the allowlist. The session has provider `cloudflare_access` and expires at the JWT's `exp`.
  `/api/auth/me` and `/api/auth/ws-ticket` work with it.
- **Everything else falls through to the normal gate**, never a 503: no header, a bad signature, wrong `aud` or `iss`,
  an expired token, an email not on the list, an unreachable certs endpoint. An Access service token has no `email`
  claim, so machine clients keep using the normal provider login.
- **`/login` with a valid Access session** redirects (302) to `next` if it is a safe path, otherwise to the app root.
  Without one, the login form renders as before.
- Requests already authenticated by the token-auth seam (`request.state.token_authenticated`) skip this check, as
  before.

## How it works

- New module `hermes_cli/dashboard_auth/cf_access.py`:
  - `load_config(env=None)` returns a `CfAccessConfig(team_domain, audiences, emails)` or `None`. The config builds
    `issuer` and `certs_url` (`https://<team>/cdn-cgi/access/certs`).
  - `_jwks_client(url)` keeps one `jwt.PyJWKClient` per certs URL, with key caching for 3600 s.
  - `verify_assertion(token, cfg)` decodes with PyJWT (`algorithms=["RS256"]`, pinned audience and issuer, 60 s
    leeway, required `exp iat aud iss sub`). It checks the email against the allowlist and builds a
    `dashboard_auth.base.Session`. It never raises; failures are logged at INFO as
    `Cloudflare Access assertion not accepted`.
  - `session_from_request(request)` reads the header and runs the above.
- `hermes_cli/dashboard_auth/middleware.py`, `gated_auth_middleware`: after the token-auth check, for `/login` or any
  non-public path that carries the header, it calls `session_from_request` in a threadpool (the JWKS fetch blocks).
  On success it sets `request.state.session` and continues, or redirects on `/login` using `is_safe_next_path` and
  `prefix_from_request`. Public paths are then passed through as before. The bearer and cookie paths are unchanged.

## Tests

`tests/hermes_cli/test_dashboard_auth_cf_access.py`, with a stubbed JWKS client and locally generated RSA keys:

- `test_valid_allowlisted_access_jwt_is_a_session`, `test_ws_ticket_works_with_access_session`.
- `test_rejected_assertions_fall_through_to_normal_gate`: wrong aud, wrong iss, other email, no email (service
  token), wrong signing key, expired. All get 401 `unauthenticated`.
- `test_disabled_without_full_config`, `test_no_header_still_requires_login`.
- `test_login_page_bounces_back_when_access_session_valid` (including an unsafe `//...` next that falls back to `/`),
  `test_login_page_renders_without_access_session`.
- `test_load_config_requires_all_settings`: normalization of domain, AUD list and emails.

## Applying

Standalone. It edits the upstream dashboard auth gate (`gated_auth_middleware`) and needs PyJWT with RSA support
(`cryptography`), which the tests import. No config file keys; settings are environment only.

## Gotchas

- Only use this when the dashboard is reachable solely through Access. The header is trusted only after signature,
  issuer and audience checks, so a forged header fails. But anyone who can reach the origin directly still sees the
  normal login gate, not a bypass.
- The allowlist is the only authorization check. Being let through Access is not enough; the email must be listed.
- The session carries no access or refresh token and no `org_id`. Code that expects provider tokens on the session
  gets empty strings.
- The header check runs on every gated request. Keys are cached for an hour, so cost after the first request is a
  local signature check.

## Plugin?

No. The dashboard auth gate has no hook for adding session sources, so this is a core edit to the middleware.
