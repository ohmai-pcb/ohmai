<!-- SPDX-License-Identifier: Apache-2.0 -->
<!-- ohmai:template: generate test/firmware/bringup_selftest.{py,c,ino} and update this README before boards arrive (phase 12), then delete this marker line. -->
# Bring-up self-test firmware (`test/firmware/`)

Write and commit ready-to-run bring-up test code here **before the boards arrive** so no code has to be developed from scratch during bench testing (`ohmai check bringup-code`).

## Required coverage
- Every non-power signal pin / GPIO in `hardware/gen/pinmap.yaml` must be exercised or explicitly documented here.
- One automated or interactive test routine per requirement `R-xx` in `test/bringup-plan.md`:
  - Power-rail sense / ADC readback and status LEDs
  - Button / switch inputs
  - Bus scans (SPI / I2C / UART / USB device enumeration) and sensor / peripheral register ID & readout checks
  - Header / edge GPIO continuity or toggling check
