# Provider decommission commands

## Fly.io (flyctl)
Suppress noise with `2>&1 | grep -v 'Metrics token'`.
- Inventory: `fly apps list`; per app: `fly machines|volumes|ips|certs|secrets list -a APP`; org-wide: `fly postgres list`, `fly redis list`, `fly tigris list`, `fly mpg list`.
- Destroy: `fly apps destroy APP --yes`. This also deletes volumes, snapshots, IPs and certs.
- Tokens: `fly tokens list -o personal`, then `fly tokens revoke ID`.
- WireGuard: `fly wireguard list personal`, then `fly wireguard remove personal NAME` for each peer. Leftover CI-runner peers pile up.
- Billing via GraphQL. Get the token with `fly auth token`, then POST to https://api.fly.io/graphql:
  `{ organization(slug:"personal"){ paidPlan billable billingStatus creditBalanceFormatted isCreditCardSaved apps{totalCount} } }`.
  Fly bills only for usage, so 0 apps means no further charges. Card removal and account closure are manual at https://fly.io/dashboard/personal/billing.
