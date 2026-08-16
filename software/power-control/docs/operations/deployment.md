# Deployment, Cutover and Rollback

These instructions target Raspberry Pi 4 with Ubuntu 24.04. Replace all values marked
`SITE_VALUE` before use. Do not expose the service through router port forwarding.

## 1. Prepare the host

Create a non-login service account and the fixed directories:

```bash
sudo groupadd --system humanoid-power
sudo useradd --system --gid humanoid-power --home /nonexistent --shell /usr/sbin/nologin humanoid-power
sudo install -d -o root -g humanoid-power -m 0750 /etc/humanoid-control
sudo install -d -o humanoid-power -g humanoid-power -m 0750 /var/lib/humanoid-control
sudo install -d -o root -g root -m 0755 /opt/humanoid-control
```

Install Python 3.12, Caddy and the approved `sispmctl` package from the managed OS sources.
Copy a reviewed release into `/opt/humanoid-control/current` and create the virtual environment:

```bash
python3.12 -m venv /opt/humanoid-control/venv
/opt/humanoid-control/venv/bin/pip install --requirement /opt/humanoid-control/current/requirements.lock
/opt/humanoid-control/venv/bin/pip install --no-deps /opt/humanoid-control/current
```

## 2. Configure secrets and database

Install `config.example.toml` as `/etc/humanoid-control/config.toml`, owned by
`root:humanoid-power` with mode 0440. Keep `hardware_accepted = false` until the hardware
acceptance document is signed.

Generate the 32-byte secret as root, make it readable only by the service account, then
initialize the database and the single administrator:

```bash
sudo sh -c 'umask 0177; openssl rand -hex 32 > /etc/humanoid-control/secret.key'
sudo chown humanoid-power:humanoid-power /etc/humanoid-control/secret.key
sudo chmod 0400 /etc/humanoid-control/secret.key
sudo -u humanoid-power HUMANOID_POWER_CONFIG=/etc/humanoid-control/config.toml \
  /opt/humanoid-control/venv/bin/humanoid-power --config /etc/humanoid-control/config.toml init-db
sudo -u humanoid-power \
  /opt/humanoid-control/venv/bin/humanoid-power --config /etc/humanoid-control/config.toml set-admin-password
sudo chown root:humanoid-power /etc/humanoid-control/config.toml
sudo chmod 0440 /etc/humanoid-control/config.toml
sudo chmod 0600 /var/lib/humanoid-control/power.db
```

Before every migration, stop the service and create a consistent SQLite backup:

```bash
sudo systemctl stop humanoid-power
sudo -u humanoid-power /opt/humanoid-control/venv/bin/humanoid-power \
  --config /etc/humanoid-control/config.toml backup-db \
  --output /var/lib/humanoid-control/power.pre-migration-YYYYMMDD-HHMMSS.db
```

## 3. USB permission

Determine the controller identity with `lsusb` and `udevadm info`. Replace both placeholders in
`deploy/udev-rule.example`, install the reviewed rule, reload udev and reconnect the controller.
Confirm access while running as `humanoid-power`; never grant sudo to the service.

## 4. Service and HTTPS

Install `deploy/humanoid-power.service` under `/etc/systemd/system/` and the reviewed Caddy site
under `/etc/caddy/Caddyfile`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now humanoid-power
sudo systemctl reload caddy
```

Trust Caddy's local root CA on each approved Pi, PC and smartphone. Restrict TCP/443 with the
host firewall to `SITE_LAN_CIDR`; do not expose TCP/8000 or configure a router forward.

Verify:

```bash
curl --fail https://humanoid.local/health/live
systemctl status humanoid-power --no-pager
journalctl -u humanoid-power --since today
```

Verify `/health/ready` from an authenticated browser session. It may return 503 while the
controller is unavailable and contains no detailed device inventory.

## 5. Cutover

1. Document all current outlet states and hardware schedules.
2. Back up the old application, configuration and database.
3. Stop and disable the legacy service.
4. Verify that no other `sispmctl` or power-control process is running.
5. Complete `docs/hardware/energenie-behavior.md` with a safe test load.
6. Set `hardware_accepted = true` only after approval.
7. Start the new service and wait for READY reconciliation.
8. Compare all four states and schedules with the recorded baseline.
9. Test each outlet with the safe test load before connecting real consumers.

Never allow the legacy and new applications to write the USB controller concurrently.

## 6. Rollback

1. Stop the new service and confirm that no operation is running.
2. Preserve its database and journal for analysis.
3. Restore the previous code/configuration and, only if required, the compatible database backup.
4. Start the legacy service alone.
5. Read all outlet states and schedules again; record every difference.

Rollback restores software. It does not restore previous relay states automatically.
