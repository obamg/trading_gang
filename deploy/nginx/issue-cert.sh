#!/usr/bin/env bash
# One-time TLS bootstrap for a NEW domain.
#
# Why this exists: nginx refuses to start when an ssl_certificate path is
# missing, but certbot's webroot challenge needs a running webserver — so the
# first certificate can't be issued with the normal prod.conf in place. This
# swaps in an HTTP-only config, issues the cert, then swaps back.
#
# Renewals do NOT need this script: the certbot service in
# docker-compose.prod.yml runs `certbot renew` twice a day and nginx reloads
# every 6h to pick up new certs.
#
# Usage (on the VPS, from /opt/trading_gang):
#   ./deploy/nginx/issue-cert.sh getmove.online admin@getmove.online
#
# Prerequisite: DNS A records for the domain AND www.<domain> must already
# resolve to this server, or Let's Encrypt validation will fail.
set -euo pipefail

DOMAIN="${1:?usage: issue-cert.sh <domain> <email>}"
EMAIL="${2:?usage: issue-cert.sh <domain> <email>}"
COMPOSE="docker compose -f docker-compose.prod.yml --env-file .env.production"

echo "==> Checking DNS for ${DOMAIN} and www.${DOMAIN}"
THIS_IP="$(curl -s --max-time 10 https://api.ipify.org || true)"
PROXIED=0
for host in "${DOMAIN}" "www.${DOMAIN}"; do
    got="$(getent hosts "${host}" | awk '{print $1}' | head -1 || true)"
    if [ -z "${got}" ]; then
        echo "    FAIL: ${host} does not resolve. Add the DNS record first." >&2
        exit 1
    fi
    echo "    ${host} -> ${got}${THIS_IP:+ (this server: ${THIS_IP})}"
    if [ -n "${THIS_IP}" ] && [ "${got}" != "${THIS_IP}" ]; then
        PROXIED=1
    fi
done

if [ "${PROXIED}" = "1" ]; then
    cat >&2 <<'WARN'
    NOTE: DNS does not resolve to this server, so a proxy (Cloudflare) is in
    front. HTTP-01 can still validate through it, but it is more fragile:
    "Always Use HTTPS" redirects the challenge, and cached/blocked paths break
    it outright.

    The reliable order for a FIRST issuance behind Cloudflare is:
      1. set the DNS records to "DNS only" (grey cloud)
      2. run this script — validation goes straight to the origin
      3. switch the records back to "Proxied" (orange cloud)
      4. set SSL/TLS mode to "Full (strict)"

    Continuing anyway in 10s (Ctrl-C to abort and do the above).
WARN
    sleep 10
fi

echo "==> Bringing nginx up on the HTTP-only bootstrap config"
# Swap the mounted config by pointing the bind-mount at the bootstrap file.
cp deploy/nginx/prod.conf /tmp/prod.conf.bak
cp deploy/nginx/prod-bootstrap.conf deploy/nginx/prod.conf
# Bootstrap config has no `nginx -s reload` loop needs; plain start is fine.
${COMPOSE} up -d --force-recreate nginx
sleep 3

echo "==> Verifying the ACME challenge path is reachable over plain HTTP"
token="bootstrap-$(date +%s)"
docker run --rm -v trading_gang_certbot_www:/w alpine:3 \
    sh -c "mkdir -p /w/.well-known/acme-challenge && echo ok > /w/.well-known/acme-challenge/${token}"
if ! curl -sf --max-time 15 "http://${DOMAIN}/.well-known/acme-challenge/${token}" | grep -q ok; then
    echo "    FAIL: challenge path not reachable from the internet." >&2
    echo "    Check that port 80 is open and DNS points here." >&2
    cp /tmp/prod.conf.bak deploy/nginx/prod.conf
    exit 1
fi
echo "    challenge reachable"

echo "==> Requesting certificate from Let's Encrypt"
${COMPOSE} run --rm --entrypoint certbot certbot certonly \
    --webroot -w /var/www/certbot \
    -d "${DOMAIN}" -d "www.${DOMAIN}" \
    --email "${EMAIL}" \
    --agree-tos --no-eff-email \
    --non-interactive

echo "==> Restoring the full TLS config"
cp /tmp/prod.conf.bak deploy/nginx/prod.conf
${COMPOSE} up -d --force-recreate nginx certbot
sleep 3

echo "==> Result"
${COMPOSE} ps nginx certbot
echo
echo "Certificate installed. Verify with:"
echo "  curl -sSI https://${DOMAIN}/api/health"
echo "  echo | openssl s_client -connect ${DOMAIN}:443 -servername ${DOMAIN} 2>/dev/null | openssl x509 -noout -dates"
