#!/usr/bin/env bash
# One-shot server setup for F-1 Opportunities on a fresh Ubuntu VM (tested shape: Oracle Always Free).
#
#   sudo bash setup.sh yourdomain.org https://github.com/YOU/REPO.git     # HTTPS on your domain
#   sudo bash setup.sh 203.0.113.9   https://github.com/YOU/REPO.git     # no domain yet: plain HTTP on the IP
#
# Re-run with the domain later to switch to HTTPS; nothing else changes.
# Installs Python + Caddy, clones the repo to /opt/f1, creates the venv, writes /etc/f1.env (secrets),
# a systemd service (uvicorn on 127.0.0.1:8000), a Caddyfile (HTTPS for the domain -> :8000) and a
# nightly cron ingest. Safe to re-run: it updates in place. Run `deploy/update.sh` after a git push.
set -euo pipefail

DOMAIN="${1:?usage: setup.sh DOMAIN REPO_URL}"
REPO="${2:?usage: setup.sh DOMAIN REPO_URL}"
APP=/opt/f1
ENV=/etc/f1.env
SVC=f1

echo "== packages"
apt-get update -q
apt-get install -y -q python3 python3-venv git sqlite3 debian-keyring debian-archive-keyring apt-transport-https curl
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

echo "== firewall (Oracle's Ubuntu image blocks 80/443 in iptables even after you open them in the console)"
for p in 80 443; do
  iptables -C INPUT -p tcp --dport $p -j ACCEPT 2>/dev/null || iptables -I INPUT 5 -p tcp --dport $p -j ACCEPT
done
command -v netfilter-persistent >/dev/null && netfilter-persistent save >/dev/null || true

echo "== app user + code"
id -u f1 >/dev/null 2>&1 || useradd --system --home $APP --shell /usr/sbin/nologin f1
if [ -d $APP/.git ]; then
  git -C $APP pull -q
else
  git clone -q "$REPO" $APP
fi
cd $APP
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
chown -R f1:f1 $APP

echo "== secrets file $ENV"
if [ ! -f $ENV ]; then
  cat > $ENV <<EOF
# Loaded by the f1 systemd service. chmod 600. Edit, then: sudo systemctl restart f1
DATABASE_URL=postgresql://...        # REQUIRED: your Neon (or any Postgres) connection string
ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
# KAGGLE_API_TOKEN=KGAT_...
# RESEND_API_KEY=re_...
# RESEND_FROM=F-1 Opportunities <hello@$DOMAIN>
# PATHWAYS_OK=1          # only after Pathways to Science says yes (docs/outreach.md)
# ANTHROPIC_API_KEY=     # only if you ever want the paid page-reading step
EOF
  chmod 600 $ENV
  echo "   wrote $ENV with a fresh ADMIN_TOKEN (paste it into /admin). Add the other keys when you have them."
fi

echo "== systemd service"
cat > /etc/systemd/system/$SVC.service <<EOF
[Unit]
Description=F-1 Opportunities (FastAPI)
After=network.target

[Service]
User=f1
WorkingDirectory=$APP
EnvironmentFile=$ENV
ExecStart=$APP/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable -q $SVC
systemctl restart $SVC

if [[ "$DOMAIN" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "== Caddy: plain HTTP on http://$DOMAIN (no domain yet; re-run with a domain for HTTPS)"
  cat > /etc/caddy/Caddyfile <<EOF
:80 {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
  URL="http://$DOMAIN"
else
  echo "== Caddy (automatic HTTPS for $DOMAIN)"
  cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN, www.$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
  URL="https://$DOMAIN"
fi
systemctl restart caddy

echo "== nightly ingest at 04:17 server time (as the f1 user, with the same secrets)"
cat > /etc/cron.d/f1-ingest <<EOF
17 4 * * * f1 cd $APP && set -a && . $ENV && set +a && .venv/bin/python ingest.py >> /var/log/f1-ingest.log 2>&1
EOF
touch /var/log/f1-ingest.log && chown f1:f1 /var/log/f1-ingest.log

echo
echo "Done. The site is at $URL  (admin: $URL/admin)"
echo "  1. Put your Postgres connection string in $ENV (DATABASE_URL=...) and: sudo systemctl restart $SVC"
echo "  2. If you used a domain: make sure its A records point at this server's public IP."
echo "  3. Admin token:  sudo grep ADMIN_TOKEN $ENV"
echo "  4. Logs:  sudo journalctl -u $SVC -f      ingest:  tail -f /var/log/f1-ingest.log"
