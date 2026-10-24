<!-- ohmai:template: fill in, then delete this line. Gate: owner approves each part with a named second source (phase 4). -->
# Part selection: {{project_name}}

One table per block. Prices: list price at the given quantity, with source and date. Never estimate a
price without labelling it `ESTIMATE:`. Stock and lifecycle come from the distributor page on that date.

## <Block name> (e.g. Buck-boost regulator)
Requirements it must meet: R-xx, R-yy

| Candidate (MPN) | Mfr | Key specs vs requirement | Package | Price @1 | @100 | @1000 | Price source + date | Stock (source, date) | Lifecycle | Second source | Datasheet (rev) | Notes / risks |
|-----------------|-----|--------------------------|---------|----------|------|-------|---------------------|----------------------|-----------|---------------|-----------------|---------------|
| | | | | | | | | | | | | |

Recommendation: _MPN, why, named second source_.
**Owner decision:** _D-xxx_. Then copy the chosen MPN + alternates into `bom/bom.csv` (status `approved`).
