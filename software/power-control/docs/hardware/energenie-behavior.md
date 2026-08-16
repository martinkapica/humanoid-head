# Energenie/Gembird SiS-PM Hardware Acceptance

Status: **NOT EXECUTED — REAL ADAPTER BLOCKED**

This document must be completed on the target Raspberry Pi with the exact controller and
`sispmctl` build. Until every blocking item passes, keep
`controller.hardware_accepted = false`.

## Safety conditions

- Use a low-risk test lamp first; do not connect robot power supplies.
- Record and isolate all existing hardware schedules before changing them.
- Stop the legacy application and verify that only one process can access the controller.
- Treat controller readback as relay-state evidence only. It does not prove voltage, current,
  downstream device operation, or safe robot shutdown.
- A qualified person must assess the real load and inrush current separately.

## Target identity

| Item | Measured value | Pass criterion |
| --- | --- | --- |
| Raspberry Pi model / OS | PENDING | Pi 4 / approved Ubuntu 24.04 image |
| `sispmctl --version` | PENDING | version recorded |
| USB vendor/product ID | PENDING | exactly one approved identity |
| Controller serial/label | PENDING | asset recorded |
| System timezone | PENDING | Europe/Berlin |
| Time synchronization | PENDING | synchronized |

## Fixture capture

For every case, store sanitized stdout, stderr, exit code and duration under
`tests/fixtures/sispmctl/`. Never include host secrets or unrelated device identifiers.

| Case | Fixture | Result |
| --- | --- | --- |
| One controller | `scan_one.txt` | PENDING |
| No controller | `scan_none.txt` | PENDING |
| More than one controller | `scan_multiple.txt` | PENDING |
| Outlet ON / OFF | `state_on.txt`, `state_off.txt` | PENDING |
| Invalid outlet | `state_invalid.txt` | PENDING |
| Empty schedule | `schedule_none.txt` | PENDING |
| One and multiple events | `schedule_one.txt`, `schedule_multiple.txt` | PENDING |
| Daily / weekly / custom | three schedule fixtures | PENDING |
| Write failure | `write_failure.txt` | PENDING |
| USB removal during access | `usb_disconnect.txt` | PENDING |

## Behaviour tests

| Test | Expected result | Actual result |
| --- | --- | --- |
| Scan with one controller | READY | PENDING |
| Scan with zero controllers | OFFLINE; writes blocked | PENDING |
| Scan with two controllers | AMBIGUOUS; writes blocked | PENDING |
| Outlets 1–4 ON then read | CONTROLLER CONFIRMED | PENDING |
| Outlets 1–4 OFF then read | CONTROLLER CONFIRMED | PENDING |
| Counter-command inside 2 s | rejected before hardware access | PENDING |
| USB removed before command | FAILED or UNKNOWN, never success | PENDING |
| USB removed during command | UNKNOWN if outcome cannot be proven | PENDING |
| Schedule write and readback | normalized structure matches | PENDING |
| Schedule delete and readback | no events and no active loop | PENDING |
| Schedule executes while app stopped | controller changes state autonomously | PENDING |
| Controller power loss | relay and schedule persistence recorded | PENDING |
| DST spring / autumn cases | behavior recorded; ambiguous input rejected | PENDING |
| Pi reboot / app restart | reconciliation before write enablement | PENDING |

## Timing measurements

Record at least 30 samples per operation and use the slowest observed value plus margin.

| Operation | Median | Maximum | Configured timeout | Pass |
| --- | ---: | ---: | ---: | --- |
| Scan | PENDING | PENDING | 3.0 s | PENDING |
| Read outlet | PENDING | PENDING | 3.0 s | PENDING |
| Set outlet + readback | PENDING | PENDING | target < 6 s | PENDING |
| Read schedule | PENDING | PENDING | 3.0 s/call | PENDING |
| Write/delete + readback | PENDING | PENDING | target < 10 s | PENDING |

## Approval

The real adapter may be enabled only when:

- fixtures match the parser tests;
- all four outlets pass with a safe test load;
- disconnect, ambiguity and readback-mismatch behavior is understood;
- udev access works without root or sudo;
- Caddy HTTPS and the LAN firewall are verified;
- the exact load and inrush-current review is signed off;
- the tester records date, app revision, controller identity and approval below.

| Field | Value |
| --- | --- |
| Tester | PENDING |
| Date | PENDING |
| App revision | PENDING |
| Decision | BLOCKED |
| Notes | PENDING |
