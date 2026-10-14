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

## Any Linux VM (Hetzner etc.): what is still running
One SSH pass (`ssh -o BatchMode=yes root@<ip>`) gives the inventory:
- `systemctl list-units --type=service --state=running --no-legend` (filter out OS units), then `systemctl cat <unit>` for `ExecStart`/`WorkingDirectory`.
- `systemctl list-timers --all --no-legend`, and `journalctl -u <unit> --since -2d -o cat | tail -3` per unit: timers that fail every run or call removed endpoints are dead weight.
- `docker ps`, `ss -tlnp`, `crontab -l` (root and service users), `df -h` including attached volumes (`/mnt/HC_Volume_*` on Hetzner are separately billed disks).
- For each data directory found, check the newest mtime (`find <dir> -newermt -30days -type f`) to tell live data from stale.

## Cloudflare tunnels on a VM
- List tunnels and where their connectors run: `GET /accounts/<acct>/cfd_tunnel?is_deleted=false` (connections[].origin_ip shows the host).
- Read ingress: `GET /accounts/<acct>/cfd_tunnel/<id>/configurations` → `result.config.ingress`. Map each hostname's `service` target to a machine (Tailscale IPs via `tailscale status`) and probe the hostname with curl (502 = dead origin, 401/200 = live).
- Before destroying the VM: add the live hostnames to a tunnel whose connector runs elsewhere, repoint DNS, re-probe; then delete the old tunnel and the DNS records of dropped hostnames.
