<!-- ohmai:template: fill in, then delete this line. Write this BEFORE boards arrive (phase 12 starts at fab order). -->
# Bring-up plan: {{project_name}} rev-A

Results go into GitHub issues (one per finding, label `rev-B`), not into this file.

## Equipment
Bench supply with current limit, DMM, scope (probe ground spring), thermal camera or thermocouple, e-load,
I2C/USB analyser.

## 0. Before power
- [ ] Visual and BOM check against the release (`bom.csv`, pin-1 and polarity of every IC/diode/cap)
- [ ] Resistance from each rail to GND (expected values from the schematic): ______

## 1. First power (current limited)
| Step | Supply V | Current limit | Expected current | Measured | Pass? |
|------|----------|---------------|------------------|----------|-------|
| 1 | low (e.g. 50 % Vin min) | lowest that starts | | | |

## 2. Rails
| Rail | Expected (min..max) | Measured | Ripple (expected / measured) | Pass? |
|------|---------------------|----------|------------------------------|-------|

## 3. Buses and firmware
- [ ] I2C scan: expected addresses ______ found ______
- [ ] Programming / debug connection

## 4. Load and thermal
| Condition | Load | Duration | Hottest part | Temp | Limit (source) | Pass? |
|-----------|------|----------|--------------|------|----------------|-------|

## 5. Functional tests (one per requirement R-xx)
| Req | Test | Expected | Measured | Pass? |
|-----|------|----------|----------|-------|
