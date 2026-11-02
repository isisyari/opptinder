# Deploying for free (Oracle Cloud Always Free + your domain)

Total cost: the domain. Everything else below is free with no expiry. You need a credit card and a
phone number to open the Oracle account; the card is identity verification and is never charged on
Always Free resources.

## 1. Put the code on GitHub

```bash
gh repo create f1-opportunities --public --source=. --push     # or create the repo on github.com and `git push`
```

Public is simplest (the server clones it without credentials). Nothing secret is in the repo: the
database is gitignored and all keys live in `/etc/f1.env` on the server.

## 2. Buy the domain (~$10/yr)

Porkbun or Cloudflare Registrar sell at cost. A `.org` or `.com` looks right to a university office
forwarding your link. You'll add DNS records in step 4.

## 3. Create the free VM

1. Sign up at cloud.oracle.com (choose a home region near your users; US East/Ashburn is fine).
2. Compute → Instances → **Create instance**.
   - Image: **Ubuntu 24.04** (minimal is fine).
   - Shape: **VM.Standard.E2.1.Micro** (Always Free, 1 GB RAM: plenty for this app) or, if available,
     `VM.Standard.A1.Flex` (ARM, up to 2 OCPU / 12 GB free). ARM is often "out of capacity"; take the Micro.
   - Networking: leave defaults; make sure **Assign a public IPv4 address** is on.
   - SSH keys: **Generate a key pair for me**, download the private key.
3. Create. Note the **Public IP**.
4. Open the web ports in the cloud firewall: Instance page → Virtual cloud network → Subnet →
   Security list → **Add Ingress Rules**: source `0.0.0.0/0`, protocol TCP, destination port `80`;
   repeat for `443`. (The setup script opens the VM's own iptables for you.)

## 4. DNS

At your registrar add two records pointing at the VM's public IP:

| Type | Name | Value |
|------|------|-------|
| A | `@`   | `<public IP>` |
| A | `www` | `<public IP>` |

Caddy gets a free HTTPS certificate automatically once these resolve (a few minutes, up to an hour).

## No domain yet?

Skip steps 2 and 4 and pass the VM's public IP instead of a domain in step 5. The site runs on
`http://<public IP>` (plain HTTP, no certificate). Everything works, including sign-in. When you buy the
domain, add the DNS records and re-run the same script with the domain; it switches to HTTPS in place.

## 5. Set up the server (one command)

```bash
chmod 600 ~/Downloads/ssh-key-*.key
ssh -i ~/Downloads/ssh-key-*.key ubuntu@<public IP>

# on the server:
curl -fsSL https://raw.githubusercontent.com/<you>/<repo>/main/deploy/setup.sh -o setup.sh
sudo bash setup.sh yourdomain.org https://github.com/<you>/<repo>.git
```

It installs Python and Caddy, clones the repo to `/opt/f1`, writes `/etc/f1.env` with a generated
`ADMIN_TOKEN`, starts the app as a systemd service, configures HTTPS, and schedules the nightly ingest.

Then copy your laptop's database up (it contains the 116 page-read eligibility results and your swipes):

```bash
# on your laptop
scp -i ~/Downloads/ssh-key-*.key opportunities.db ubuntu@<public IP>:/tmp/
# on the server
sudo mv /tmp/opportunities.db /opt/f1/ && sudo chown f1:f1 /opt/f1/opportunities.db && sudo systemctl restart f1
```

Open `https://yourdomain.org`. Swipe. Open `https://yourdomain.org/admin`, paste the token from
`sudo grep ADMIN_TOKEN /etc/f1.env`.

## 6. Keys (add as you get them)

```bash
sudo nano /etc/f1.env       # then: sudo systemctl restart f1
```

- `KAGGLE_API_TOKEN` so the nightly ingest includes Kaggle (kaggle.com → Settings → API).
- `RESEND_API_KEY` and `RESEND_FROM` so sign-in codes are emailed (resend.com, free; verify your domain
  there by adding the DNS records it shows you). Until then codes print to `journalctl -u f1`.
- `PATHWAYS_OK=1` only after Pathways to Science replies yes to the email in `docs/outreach.md`.

## 7. Day-to-day

| Task | Command (on the server) |
|------|-------------------------|
| Deploy a change after `git push` | `sudo bash /opt/f1/deploy/update.sh` |
| App logs | `sudo journalctl -u f1 -f` |
| Last ingest | `tail -50 /var/log/f1-ingest.log` |
| Run ingest now | `sudo -u f1 bash -c 'cd /opt/f1 && set -a && . /etc/f1.env && set +a && .venv/bin/python ingest.py'` |
| Back up the database | from laptop: `scp -i <key> ubuntu@<ip>:/opt/f1/opportunities.db ./backup-$(date +%F).db` |

The GitHub Actions workflow in `.github/workflows/ingest.yml` is optional with this setup (cron on the
VM does the same job). Leave it disabled unless you'd rather see ingest results in the Actions tab.

## If Oracle signup fails or has no capacity

Google Cloud's `e2-micro` in a US region is also always free (same card-on-file rule); the script works
unchanged on its Ubuntu image, and GCP's firewall is a checkbox ("Allow HTTP/HTTPS traffic") at VM
creation. Failing both, the cheapest paid option that keeps SQLite on disk is a Fly.io machine with a
volume at roughly $2-3/month.
