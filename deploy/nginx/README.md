# Public vhost configs for the TradeCore VPS

The VPS runs a shared `toxal-nginx` container (at `/opt/toxal`) that owns
ports 80 and 443 and terminates TLS for every stack on the box (Toxal,
Comptamatieregolden, TradeCore). This directory holds **TradeCore's**
vhost files, version-controlled here so we can diff changes without SSHing.

## Topology

```
 internet ──► :443 toxal-nginx ──► 127.0.0.1:8090 (loopback)
                                    │
                                    └─► tradecore_frontend:80 (docker)
                                          └─► api:8000 (docker, internal)
```

- TradeCore's `frontend` container publishes `127.0.0.1:8090:80` in
  `docker-compose.prod.yml` — reachable from the VPS host only, never
  from the public IP.
- `toxal-nginx` proxies to that port via the gateway IP of its own
  bridge network (`toxal_toxal`).
- Certs live in `/opt/toxal/certbot/conf/live/<domain>/` and are issued
  by the existing `toxal-certbot` container.

## Files

| File | Domains | Upstream |
|------|---------|----------|
| `formationdouane.online.conf` | `formationdouane.online`, `www.`, `app.` | `__GATEWAY_IP__:8090` |

## Deploying a vhost

One-time per file (not auto-synced with git pulls):

```bash
# 1. Resolve the bridge gateway IP
GW=$(docker network inspect toxal_toxal --format '{{(index .IPAM.Config 0).Gateway}}')

# 2. Render the template on the host
sed "s/__GATEWAY_IP__/$GW/g" \
    /opt/tradecore/deploy/nginx/formationdouane.online.conf \
    | sudo tee /opt/toxal/nginx/conf.d/formationdouane.online.conf > /dev/null

# 3. First-time only: comment out the 443 block, issue the cert, uncomment
#    (see inline instructions at the top of the .conf file).

# 4. Validate + reload
docker exec toxal-nginx nginx -t && docker exec toxal-nginx nginx -s reload
```

## Rotating / renewing certs

Certbot is already cron-renewed inside the `toxal-certbot` container for
the existing domains. Once a new cert is issued via the one-time
`docker compose run --rm certbot certonly ...` command, it joins the
auto-renewal loop — nothing extra to configure.

## Adding a new domain

1. Copy an existing `.conf` file in this directory, rename, edit
   `server_name` and the proxy upstream port.
2. Follow the "Deploying a vhost" steps above.
3. Commit the new file so the config is tracked.
