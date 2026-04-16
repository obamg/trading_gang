# Hostinger VPS deployment

Every push to `main` triggers `.github/workflows/deploy.yml`. The workflow
detects which paths changed and deploys **only** the affected services to the
Hostinger VPS. Unchanged services are left running — no rebuild, no restart,
no downtime.

## Change → deploy mapping

| Changed path                      | Rebuilt image   | Services restarted on VPS     |
| --------------------------------- | --------------- | ----------------------------- |
| `tradecore/**`                    | `api`           | `api` + `scheduler`           |
| `frontend/**`                     | `frontend`      | `frontend`                    |
| `docker-compose.prod.yml`         | both            | everything                    |
| `scripts/deploy-remote.sh`        | —               | everything                    |
| `.github/workflows/deploy.yml`    | both            | everything                    |
| anything else (docs, md, etc.)    | —               | nothing                       |

Manual override: run `Deploy to production` via **Actions → Run workflow** and
check **force\_full\_deploy** to rebuild & redeploy every service.

## One-time GitHub setup

Create a **Production** environment (Settings → Environments → New environment)
with a manual approval gate if you want a human click before every release.
Then add these **secrets** to that environment:

| Secret                     | What it is                                                              |
| -------------------------- | ----------------------------------------------------------------------- |
| `HOSTINGER_SSH_HOST`       | VPS IP or hostname (e.g. `203.0.113.42`)                                |
| `HOSTINGER_SSH_PORT`       | SSH port — omit if using default `22`                                   |
| `HOSTINGER_SSH_USER`       | Deploy user, e.g. `deploy` or `root`                                    |
| `HOSTINGER_SSH_KEY`        | Full private key (OpenSSH format, no passphrase) matching the VPS user  |
| `HOSTINGER_DEPLOY_DIR`     | Absolute path on VPS, e.g. `/opt/tradecore` (default if unset)          |
| `HOSTINGER_HEALTH_URL`     | Optional: public URL of `/api/health` for a post-deploy smoke test      |

## One-time VPS bootstrap

Run these once on the Hostinger VPS (SSH in as root or a sudoer):

```bash
# 1. Install docker + compose v2
curl -fsSL https://get.docker.com | sh
apt-get install -y docker-compose-plugin

# 2. Create a deploy user (optional but recommended over root-only)
adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh
# paste your CI public key into /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh && chmod 600 /home/deploy/.ssh/authorized_keys

# 3. Deploy dir + env file (keep .env.production OFF git)
mkdir -p /opt/tradecore
chown deploy:deploy /opt/tradecore
# copy your real .env.production to /opt/tradecore/.env.production
#   — use .env.production.example as the template
chmod 600 /opt/tradecore/.env.production

# 4. Log in to GHCR so `docker compose pull` can grab private images.
#    Create a classic PAT with the `read:packages` scope at
#    https://github.com/settings/tokens and run as the `deploy` user:
sudo -u deploy bash -c 'echo <YOUR_PAT> | docker login ghcr.io -u <github_username> --password-stdin'

# 5. First run — let CI push the images, then on the VPS:
cd /opt/tradecore
docker compose -f docker-compose.prod.yml --env-file .env.production pull
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

After this first boot, all subsequent deploys happen automatically from
GitHub Actions when you `git push` to `main`.

## What actually runs on each push

1. **`changes`** — detects modified paths via `dorny/paths-filter`.
2. **`backend-tests`** — runs only if `tradecore/**` (or infra/force) changed.
3. **`frontend-tests`** — runs only if `frontend/**` (or infra/force) changed.
4. **`build-api`** / **`build-frontend`** — each builds and pushes its image
   to GHCR, tagged as both `:latest` and `:<git-sha>`. Skipped if the source
   tree for that service didn't change. GitHub Actions cache (`type=gha`) keeps
   repeat builds fast.
5. **`deploy`** —
   - computes the list of services to restart (see mapping table above)
   - `scp`s the latest `docker-compose.prod.yml` + `deploy-remote.sh` to the VPS
   - `ssh`s in and runs `deploy-remote.sh <api-sha> <frontend-sha> <services…>`
   - the remote script pulls **only those services** and recreates them with
     `docker compose up -d --no-deps`, leaving Postgres/Redis untouched
   - hits `/api/health` afterward if `HOSTINGER_HEALTH_URL` is configured

## Rolling back

Every successful build is tagged with the commit sha in GHCR. To roll back:

```bash
ssh deploy@<vps>
cd /opt/tradecore
export API_IMAGE_TAG=<previous_sha> FRONTEND_IMAGE_TAG=<previous_sha>
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Or re-run the earlier successful workflow from the Actions tab.
