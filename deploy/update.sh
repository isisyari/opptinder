#!/usr/bin/env bash
# Pull the latest code and restart. Run on the server after you `git push`:  sudo bash /opt/f1/deploy/update.sh
set -euo pipefail
cd /opt/f1
git pull -q
.venv/bin/pip install -q -r requirements.txt
chown -R f1:f1 /opt/f1
systemctl restart f1
systemctl --no-pager --lines=5 status f1
