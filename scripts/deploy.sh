#!/usr/bin/env bash
# TradeCore first-time VPS setup + deploy script
# Target: Ubuntu 24, dedicated VPS at 187.124.221.169
#
# Usage (from your local machine):
#   ssh root@187.124.221.169 'bash -s' < scripts/deploy.sh
#
# Or copy the repo first and run on the VPS:
#   scp -r . root@187.124.221.169:/opt/tradecore/
#   ssh root@187.124.221.169 'cd /opt/tradecore && bash scripts/deploy.sh'

set -euo pipefail

DOMAIN="formationdouane.online"
APP_DIR="/opt/tradecore"
COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env.production"

log() { printf '\n\033[1;34m[tradecore]\033[0m %s\n' "$*"; }

# ── 1. System packages ──
log "Updating system and installing Docker..."
apt-get update -qq
apt-get upgrade -y -qq
apt-get install -y -qq curl git ufw

# Install Docker if missing
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
fi

# Docker Compose plugin
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

# ── 2. Firewall ──
log "Configuring firewall..."
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

# ── 3. Swap (if < 2GB RAM) ──
if [ ! -f /swapfile ]; then
    log "Creating 2GB swap..."
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ── 4. App directory ──
log "Setting up $APP_DIR..."
mkdir -p "$APP_DIR"

if [ ! -f "$APP_DIR/$COMPOSE_FILE" ]; then
    log "ERROR: $APP_DIR/$COMPOSE_FILE not found."
    log "Copy the repo to $APP_DIR first:"
    log "  scp -r . root@187.124.221.169:$APP_DIR/"
    exit 1
fi

cd "$APP_DIR"

# ── 5. Environment file ──
if [ ! -f "$ENV_FILE" ]; then
    log "Creating $ENV_FILE from template..."
    cp .env.production.example "$ENV_FILE"

    # Auto-generate secrets
    JWT_SECRET=$(openssl rand -hex 32)
    APP_SECRET_KEY=$(openssl rand -hex 32)
    PG_PASSWORD=$(openssl rand -hex 16)
    ENCRYPTION_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())" 2>/dev/null || openssl rand -base64 32)

    sed -i "s|^JWT_SECRET=.*|JWT_SECRET=$JWT_SECRET|" "$ENV_FILE"
    sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$APP_SECRET_KEY|" "$ENV_FILE"
    sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$PG_PASSWORD|" "$ENV_FILE"
    sed -i "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=$ENCRYPTION_KEY|" "$ENV_FILE"

    log "Secrets auto-generated in $ENV_FILE"
    log "Edit $ENV_FILE to add Stripe, Google OAuth, and API keys before continuing."
    log "Then re-run this script."
    exit 0
fi

# ── 6. DNS check ──
log "Checking DNS for $DOMAIN..."
RESOLVED_IP=$(dig +short "$DOMAIN" 2>/dev/null | tail -1)
VPS_IP=$(curl -s4 ifconfig.me)
if [ "$RESOLVED_IP" != "$VPS_IP" ]; then
    log "WARNING: $DOMAIN resolves to $RESOLVED_IP, but this VPS is $VPS_IP"
    log "Update your DNS A record to point to $VPS_IP before issuing TLS cert."
fi

# ── 7. Bootstrap: start with HTTP-only nginx to get TLS cert ──
CERT_VOLUME="$(basename "$APP_DIR")_certbot_certs"
CERT_EXISTS=$(docker volume ls -q | grep -c "$CERT_VOLUME" || true)

# Check if cert already exists inside the volume
HAS_CERT=0
if [ "$CERT_EXISTS" -gt 0 ]; then
    HAS_CERT=$(docker run --rm -v "${CERT_VOLUME}:/certs" alpine sh -c \
        "test -f /certs/live/$DOMAIN/fullchain.pem && echo 1 || echo 0")
fi

if [ "$HAS_CERT" -eq 0 ]; then
    log "No TLS cert yet — starting with HTTP-only bootstrap config..."
    # Temporarily use bootstrap nginx config
    cp deploy/nginx/prod.conf deploy/nginx/prod.conf.bak
    cp deploy/nginx/prod-bootstrap.conf deploy/nginx/prod.conf

    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build
    log "Waiting for nginx to be ready..."
    sleep 5

    # Issue TLS cert
    log "Issuing TLS certificate for $DOMAIN..."
    CERTBOT_EMAIL=$(grep '^CERTBOT_EMAIL=' "$ENV_FILE" | cut -d= -f2)
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" run --rm certbot certonly \
        --webroot -w /var/www/certbot \
        -d "$DOMAIN" -d "www.$DOMAIN" \
        --email "${CERTBOT_EMAIL:-admin@$DOMAIN}" \
        --agree-tos --no-eff-email

    # Restore full TLS config and restart nginx
    mv deploy/nginx/prod.conf.bak deploy/nginx/prod.conf
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build
    log "TLS certificate issued. Full stack running."
else
    log "TLS cert exists. Starting full stack..."
    docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" up -d --build
fi

sleep 10
docker compose -f "$COMPOSE_FILE" ps

# ── 9. Certbot auto-renewal cron ──
CRON_CMD="0 3 * * * cd $APP_DIR && docker compose -f $COMPOSE_FILE run --rm certbot renew --quiet && docker compose -f $COMPOSE_FILE restart nginx"
if ! crontab -l 2>/dev/null | grep -q "certbot renew"; then
    (crontab -l 2>/dev/null; echo "$CRON_CMD") | crontab -
    log "Certbot auto-renewal cron added (daily at 3 AM)."
fi

log "Deployment complete!"
log "  https://$DOMAIN"
log "  API: https://$DOMAIN/api/health"
echo ""
docker compose -f "$COMPOSE_FILE" ps
