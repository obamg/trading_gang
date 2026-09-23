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

The env file is **`.env.production`**, not `.env` — `deploy-remote.sh` runs
`docker compose ... --env-file .env.production`. There is no `.env`; looking for
one wastes a round trip.

Pipe it host-to-host so the secrets never land on your own disk:

```bash
ssh root@OLD_HOST 'cat /opt/trading_gang/.env.production' \
  | ssh root@NEW_HOST 'umask 077; cat > /opt/trading_gang/.env.production'
```

- **`ENCRYPTION_KEY`** — the Fernet key for `exchange_credentials`. Lose it and
  every connected user's exchange API keys are permanently undecryptable. There
  is no recovery path; they would have to re-enter them.
- `JWT_SECRET`, `APP_SECRET_KEY` — changing these only invalidates sessions.

Verify before going further:

```bash
ssh root@NEW_HOST "grep -c ENCRYPTION_KEY /opt/trading_gang/.env.production"
```

Checking the key is *present* is not enough — verify it actually **decrypts**,
once the stack is up. This is the only test that proves the migration is safe:

```bash
docker exec trading_gang-api-1 python -c "
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.exchange import ExchangeCredential
from app.services.exchanges.credentials import load_credentials
async def main():
    async with AsyncSessionLocal() as db:
        for r in (await db.execute(select(ExchangeCredential))).scalars().all():
            c = load_credentials(r)   # raises if ENCRYPTION_KEY is wrong
            print(r.exchange, 'DECRYPT OK', len(c.api_key), len(c.api_secret))
asyncio.run(main())
"
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

Measured on the 2026-09-23 move: ~30 minutes end to end, of which the transfer
was 7 seconds.

```bash
# 1. stop the old stack — this ends all writes
ssh root@OLD_HOST 'cd /opt/trading_gang && \
  docker compose -f docker-compose.prod.yml --env-file .env.production stop api scheduler'

# 2. final dumps: Postgres AND a full Redis RDB (see §5 — do not cherry-pick keys)
ssh root@OLD_HOST 'docker exec trading_gang-postgres-1 pg_dump -U tradecore -Fc tradecore > /root/final.dump
  docker exec trading_gang-redis-1 redis-cli --rdb /tmp/redis-final.rdb
  docker cp trading_gang-redis-1:/tmp/redis-final.rdb /root/redis-final.rdb
  ls -lh /root/final.dump /root/redis-final.rdb'
```

**Transfer host-to-host, not through your laptop.** 526MB took 6.7s on the
datacenter link and would take many minutes over a home connection while the
site is down. Create an ephemeral keypair on the source, and delete it after:

```bash
PUB=$(ssh root@OLD_HOST 'ssh-keygen -q -t ed25519 -N "" -f /root/.ssh/migrate_tmp -C tmp-migration <<< y >/dev/null 2>&1; cat /root/.ssh/migrate_tmp.pub')
ssh root@NEW_HOST "printf '%s\n' '$PUB' >> /root/.ssh/authorized_keys"
ssh root@OLD_HOST 'scp -i /root/.ssh/migrate_tmp /root/final.dump /root/redis-final.rdb root@NEW_HOST:/root/'
# afterwards: remove migrate_tmp on OLD and strip the line from NEW's authorized_keys
```

Restore Postgres with `pg_dump`/`pg_restore`, never a copy of the `pg_data`
volume — a volume copied from a running database is a torn snapshot:

```bash
ssh root@NEW_HOST 'cd /opt/trading_gang && docker compose -f docker-compose.prod.yml \
  --env-file .env.production up -d postgres'
# wait for healthy, then:
ssh root@NEW_HOST 'docker exec -i trading_gang-postgres-1 \
  pg_restore -U tradecore -d tradecore --clean --if-exists --no-owner < /root/final.dump'
```

Also copy the CI deploy key across, or the next deploy fails:

```bash
ssh root@OLD_HOST 'cat /root/.ssh/authorized_keys' \
  | ssh root@NEW_HOST 'cat >> /root/.ssh/authorized_keys && sort -u /root/.ssh/authorized_keys -o /root/.ssh/authorized_keys'
```

## 5. Redis — migrate the whole RDB, and mind the AOF

Most Redis keys regenerate within a tick (candles, funding, cooldowns,
force-subscribe sets). These do not:

| Key | Consequence of losing it |
| --- | --- |
| `listingwatch:known` | It is the **diff target**. Empty ⇒ the first tick treats every listing as new ⇒ alert storm. Was 2,779 members |
| `walletwatch:cursor:{chain}:{addr}` | Per-chain ingest restarts from scratch |
| `awakening:baseline`, `awakening:hist:*` | The 7-day sleepy baseline restarts empty |

Rather than cherry-picking those, migrate the whole RDB — it is simpler and
cannot miss anything. **The trap:** the service runs `--appendonly yes`, so
Redis loads the **AOF** and will silently ignore a `dump.rdb` you drop in. You
must load the RDB with AOF *off*, then convert:

```bash
# place the snapshot in the volume
docker run --rm -v trading_gang_redis_data:/data -v /root:/host:ro alpine \
  cp /host/redis-final.rdb /data/dump.rdb

# load it with appendonly OFF
docker run -d --name redis_restore -v trading_gang_redis_data:/data redis:7-alpine \
  redis-server --appendonly no --maxmemory 512mb --maxmemory-policy allkeys-lru

# WAIT for the load to finish — a 98MB RDB answers LOADING for several seconds
# and CONFIG SET is rejected during it. Poll until DBSIZE returns a number:
until docker exec redis_restore redis-cli DBSIZE 2>&1 | grep -qv LOADING; do sleep 2; done
docker exec redis_restore redis-cli SCARD listingwatch:known   # sanity vs source

# convert to AOF so the real service picks it up, then discard the helper
docker exec redis_restore redis-cli CONFIG SET appendonly yes
until docker exec redis_restore redis-cli INFO persistence | grep -q "aof_rewrite_in_progress:0"; do sleep 2; done
docker stop redis_restore && docker rm redis_restore
```

Expect a small key shortfall versus the source (9,640 → 8,923 on the real move)
— that is TTL'd keys expiring between snapshot and load, not data loss.

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

**Copy the certificates; do not re-issue them.** They are bound to the domain,
not the IP, so they work unchanged from the new origin — and re-issuing burns
against Let's Encrypt's limit of 5 duplicate certificates per week.

```bash
ssh root@OLD_HOST 'docker run --rm -v trading_gang_letsencrypt:/le:ro alpine \
  tar czf - -C /le . > /root/letsencrypt.tgz'
# transfer as in §4, then on NEW:
docker volume create trading_gang_letsencrypt
docker run --rm -v trading_gang_letsencrypt:/le -v /root:/host:ro alpine \
  tar xzf /host/letsencrypt.tgz -C /le
```

Bring nginx up **before** touching DNS, and prove the new box serves the real
domain by forcing resolution — this is the last safe moment to find a problem:

```bash
curl --resolve getmove.online:443:NEW_IP https://getmove.online/api/health
curl -o /dev/null -w '%{http_code}\n' --resolve getmove.online:443:NEW_IP \
  -X POST https://getmove.online/api/auth/login \
  -H 'Content-Type: application/json' -d '{"email":"x@invalid.example","password":"wrong"}'
```

Expect health `ok` and **401** on the POST — 401 means the request reached the
API. A GET-only check is not enough: the 2026 CDN outage had GETs returning 200
while every POST was reset at the proxy, so login was dead while health looked
perfect.

Then flip the Cloudflare record to the new IP. It is proxied, so propagation is
near-instant. Afterwards re-check the Cloudflare real-IP / rate-limit config —
the origin changed, and any allowlist pinned to the old address must be updated.

## 8. Verify

```bash
curl -s https://getmove.online/api/health
```

Expect `db`, `redis` and `binance_stream` all `ok`. Note the public path is
`/api/health` (nginx rewrites) but **inside the container it is `/health`** —
`/api/health` 404s there.

Confirm traffic actually moved, rather than assuming:

```bash
ssh root@NEW_HOST 'docker logs --since 60s trading_gang-nginx-1 | wc -l'   # >0
ssh root@OLD_HOST 'docker logs --since 60s trading_gang-nginx-1 | wc -l'   # 0
``` Then confirm within the
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
