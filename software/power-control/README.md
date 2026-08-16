# Humanoid Control – Power V1

Structured replacement for the current single-file Flask prototype. The project controls one
four-outlet Energenie/Gembird SiS-PM controller and implements the approved V1 technical
specification with a state cache, one serial hardware queue, readback verification, SQLite,
one administrator and a responsive local web interface.

## Safety boundary

`CONTROLLER CONFIRMED` means that the Energenie controller reports the requested relay state.
It does not prove voltage, current flow, downstream device operation, or a safe robot shutdown.
This application is not an emergency stop or a safety controller.

The real adapter is fail-closed by configuration. Startup with `adapter = "sispmctl"` raises an
error until `controller.hardware_accepted = true` has been set after completing the hardware
acceptance checklist.

## Profiles

| Function | MONITOR | DIRECT | TIMED |
| --- | ---: | ---: | ---: |
| Read state and schedules | yes | yes | yes |
| Manual switching | no | yes | yes |
| Write/delete schedules | no | no | yes |
| Active hardware schedules allowed | yes | no | yes |

Changing a profile never switches an outlet or silently deletes a hardware schedule.

## Architecture

```text
Browser → Caddy/HTTPS → Gunicorn (1 worker, 4 threads) → Flask
                                                       ↓
SQLite ← services ← one FIFO operation worker ← shared adapter lock → sispmctl
                  ↖ state poller/cache ↗
```

All pages read from the cache. No route executes a hardware subprocess directly.

## Development setup

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --requirement requirements-dev.lock
python -m pip install --no-deps --editable .
cp config.development.example.toml config.local.toml
```

The development example already selects the fake adapter, local writable paths and
`hardware_accepted = false`.

Create a development administrator and start with the fake adapter:

```bash
humanoid-power --config config.local.toml init-db
humanoid-power --config config.local.toml set-admin-password
humanoid-power --config config.local.toml serve --fake-adapter
```

The real `sispmctl` adapter remains blocked until the hardware fixtures and hardware acceptance
tests documented in `docs/hardware/energenie-behavior.md` are complete.

## Test

```bash
pytest --cov=humanoid_power --cov-report=term-missing
ruff check .
mypy src/humanoid_power
```

The delivered test suite covers domain validation, fixture parsing, database migrations, queue
serialization and idempotency, schedule/DST rules, end-to-end web commands, profile conflicts,
CSRF, login lockout, recent-login enforcement, secure cookies and security headers. Hardware
tests are opt-in and remain blocked until the real fixtures exist.

## Production topology

```text
Browser → HTTPS/Caddy → 127.0.0.1:8000/Gunicorn → Flask → one hardware queue → sispmctl
```

Only one application process may own the USB controller.

Production files are under `deploy/`; the step-by-step host setup, backup, cutover and rollback
procedure is in `docs/operations/deployment.md`. Do not enable router port forwarding.
