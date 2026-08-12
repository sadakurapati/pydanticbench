# Scorer self-test

Verifies scoring behaviour against known-value inputs (2-task sample, local backend).

| Control | n | Mean score | Expected | Result |
|---|---:|---:|---|---|
| formula | 6 | 1.000 | exact | PASS |
| oracle | 2 | 1.000 | 1.000 | PASS |
| noop | 2 | 0.000 | 0.000 | PASS |
| cheat | 2 | 0.000 | 0.000 (gated) | PASS |
| half | 5 | 0.000 | < 1.000 | PASS |
