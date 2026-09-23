# Moving the production VPS to a new host

Written for the Hostinger → Hetzner move, but nothing here is provider-specific.

**Take a short maintenance window.** Running both stacks at once is the one
approach to avoid: each box has its own Postgres and Redis, so the leader
election in `app/main.py` cannot see the other side. Both would run the
singletons — the market-data stream, the Telegram bot, the LiquidMap listener —
and both would open MajorsBot paper trades into *different* databases. The
volevent and newsevent forward tests are live and un-repeatable, so a diverged
ledger is real damage. Ten minutes of downtime is much cheaper than
reconciling two ledgers.

---

## 0. Before you buy anything — check the new IP is not filtered

Exchange APIs block by IP reputation, and a datacenter range can be treated very
differently from provider to provider. Spin up the cheapest hourly instance in
the target location, run this, then destroy it (costs about €0.01):

```bash
for u in \
  "https://api.bybit.com/v5/market/time" \
  "https://api.binance.com/api/v3/time" \
  "https://fapi.binance.com/fapi/v1/time" \
  "https://api.coingecko.com/api/v3/ping" \
; do printf '%-46s ' "$u"; curl -sS -o /dev/null -m 10 -w '%{http_code}\n' "$u"; done
```

`api.bybit.com` returning **200** is the go/no-go — it is `MARKET_DATA_SOURCE`.
A `451` from Binance means the jurisdiction is restricted; a `403` means the IP
is. Either is a reason to pick a different location or provider.

> Result for Hetzner Falkenstein, 2026-09-23: all 200. Bybit latency ~190 ms,
> which is irrelevant at MajorsBot's 5-minute tick.

## 1. Provision and bootstrap

Ubuntu 24.04, x86. **Not Arm64** — CI sets no `platforms:` key, so the images
are amd64 only and would not run.

```bash
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin
mkdir -p /opt/trading_gang/deploy/nginx
```

Add your deploy public key to `/root/.ssh/authorized_keys` (mode 600, directory
700, **one line per key** — a wrapped paste is silently ignored).

## 2. Copy the secrets — `ENCRYPTION_KEY` is the one that cannot be regenerated

`.env` is not in git. Copy it byte-exact:

```bash
scp root@OLD_HOST:/opt/trading_gang/.env /tmp/prod.env
scp /tmp/prod.env root@NEW_HOST:/opt/trading_gang/.env
shred -u /tmp/prod.env
```

- **`ENCRYPTION_KEY`** — the Fernet key for `exchange_credentials`. Lose it and
  every connected user's exchange API keys are permanently undecryptable. There
  is no recovery path; they would have to re-enter them.
- `JWT_SECRET`, `APP_SECRET_KEY` — changing these only invalidates sessions.

Verify before going further:

```bash
ssh root@NEW_HOST "grep -c ENCRYPTION_KEY /opt/trading_gang/.env"   # expect 1
```

## 3. Dry run — restore a practice dump and confirm the stack boots

Do this *before* the maintenance window so the window itself holds no surprises.

```bash
# on OLD
docker exec trading_gang-postgres-1 pg_dump -U tradecore -Fc tradecore > /tmp/practice.dump
# copy to NEW, restore, bring the stack up, then STOP it again
```

Bring it up with **the bots off** so a dry run cannot write paper trades:

```bash
MAJORSBOT_ENABLED=false TELEGRAM_BOT_ENABLED=false \
  docker compose -f docker-compose.prod.yml up -d
curl -s http://NEW_HOST/api/health     # expect db/redis ok
docker compose -f docker-compose.prod.yml down
```

## 4. The maintenance window

```bash
# 1. stop the old stack — this ends all writes
ssh root@OLD_HOST 'cd /opt/trading_gang && \
  docker compose -f docker-compose.prod.yml stop api scheduler'

# 2. final dump
ssh root@OLD_HOST 'docker exec trading_gang-postgres-1 \
  pg_dump -U tradecore -Fc tradecore' > final.dump

# 3. preserve the Redis keys that do NOT rebuild themselves (see §5)
ssh root@OLD_HOST 'docker exec trading_gang-redis-1 \
  redis-cli --no-raw SMEMBERS listingwatch:known' > listingwatch_known.txt

# 4. restore on NEW
cat final.dump | ssh root@NEW_HOST 'docker exec -i trading_gang-postgres-1 \
  pg_restore -U tradecore -d tradecore --clean --if-exists'

# 5. start NEW fully
ssh root@NEW_HOST 'cd /opt/trading_gang && \
  docker compose -f docker-compose.prod.yml up -d'
```

Use `pg_dump`/`pg_restore`, never a copy of the `pg_data` volume — a volume
copied from a running database is a torn snapshot.

## 5. Redis state that does not rebuild

Most Redis keys regenerate within a tick (candles, funding, cooldowns,
force-subscribe sets). These do not, and losing them has visible consequences:

| Key | Consequence of losing it |
| --- | --- |
| `listingwatch:known` | It is the **diff target**. Empty ⇒ the first tick treats every listing as new ⇒ alert storm |
| `walletwatch:cursor:{chain}:{addr}` | Per-chain ingest restarts from scratch; re-ingest cost and possible duplicate alerts |
| `awakening:baseline`, `awakening:hist:*` | The 7-day sleepy baseline restarts empty; no awakening signals until it refills |

If you skip restoring `listingwatch:known`, **disable `LISTINGWATCH_ENABLED` for
one tick** after cutover, let it repopulate, then re-enable.

## 6. Repoint the deploy

1. Capture the new host keys:
   ```bash
   ssh-keyscan -t rsa,ecdsa,ed25519 NEW_HOST
   ```
2. Settings → Environments → **production**, set:
   - `DEPLOY_SSH_HOST` → new IP
   - `DEPLOY_SSH_USER`, `DEPLOY_SSH_KEY` → as appropriate
   - `DEPLOY_SSH_KNOWN_HOSTS` → the full `ssh-keyscan` output, verbatim
   - `DEPLOY_HEALTH_URL`, `DEPLOY_REMOTE_DIR` if you used the old equivalents
3. Leave the `HOSTINGER_*` secrets in place until the first deploy to the new
   host is green — the workflow prefers `DEPLOY_*` and falls back to them.
4. Once green, delete the `HOSTINGER_*` secrets and remove the fallback branch
   in the *Trust VPS host key* step of `.github/workflows/deploy.yml`.

## 7. DNS and certificates

Certificates are easier to re-issue than to migrate, but DNS must point at the
new host first, and Let's Encrypt allows only **5 duplicate certificates per
week** — do not burn retries by testing repeatedly.

1. Lower the Cloudflare TTL well in advance.
2. Point the record at the new IP.
3. Issue certs on the new host.
4. Re-check the Cloudflare real-IP / rate-limit configuration; the origin IP
   changed and any allowlist pinned to the old address must be updated.

## 8. Verify

```bash
curl -s https://getmove.online/api/health
```

Expect `db`, `redis` and `binance_stream` all `ok`. Then confirm within the
first hour:

- the market-data stream is filling candles (a leader was elected)
- MajorsBot's tick logs are clean, with no duplicate open positions
- the Telegram bot is sending exactly once, not twice
- no listing-alert storm (see §5)

## 9. Rollback

Keep the old VPS running, stack stopped, for at least a week. Rolling back is:
point DNS back, `docker compose up -d` on the old host. The only loss is data
written on the new host in between — which is why the old box must stay
*stopped* rather than *deleted*, and why nothing should be decommissioned until
you have seen a full weekly cycle including the Monday 08:00 UTC report.

## 10. Afterwards

- Update the `reference_vps` memory note with the new IP and provider.
- Update the pinned host-keys comment date in the workflow if you removed the
  fallback.
