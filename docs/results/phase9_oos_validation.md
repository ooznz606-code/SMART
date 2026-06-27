# Phase 9 — Out-of-Sample Validation

**OOS window:** 2026-05-01 → 2026-06-25 (38 trading days, 2 months)  
**Candidates:** 7 global parameter portfolios + 1 symbol-specific portfolio  
**Gate:** PF > baseline OOS PF  AND  TotalR > baseline OOS TotalR  AND  MaxDD ≤ baseline OOS MaxDD  AND  n ≥ baseline OOS n  

> First and only inspection of OOS data. No modifications made after seeing results.

---

## OOS Baseline

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 34 | 0.895 | 4.47 | 47.1% | 1.54 | +9.72R | 8.00R | +0.286R |

**Baseline monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 18 | 33.3% | 0.90 | -1.20R |
| 2026-06 | 16 | 62.5% | 2.82 | +10.92R |

---

## Results Summary

| Candidate | Type | Syms | n | T/Day | WR | PF | TotalR | MaxDD | AvgR | May | Jun | Gate |
|-----------|------|-----:|--:|------:|---:|---:|-------:|------:|-----:|----:|----:|:----:|
| H-02+H-05+H-08 | GLOBAL | 7 | 29 | 0.763 | 34.5% | 0.95 | -1.00R | 9.00R | -0.034R | -4.80R | +3.80R | FAIL ✗ |
| H-01+H-05+H-08 | GLOBAL | 7 | 26 | 0.684 | 38.5% | 1.12 | +2.00R | 8.00R | +0.077R | -3.80R | +5.80R | FAIL ✗ |
| H-05+H-06+H-08 | GLOBAL | 8 | 26 | 0.684 | 38.5% | 1.12 | +2.00R | 8.00R | +0.077R | -3.80R | +5.80R | FAIL ✗ |
| H-01+H-02+H-08 | GLOBAL | 8 | 35 | 0.921 | 37.1% | 1.06 | +1.40R | 10.00R | +0.040R | -6.00R | +7.40R | FAIL ✗ |
| H-02+H-08 | GLOBAL | 8 | 34 | 0.895 | 38.2% | 1.11 | +2.40R | 9.00R | +0.071R | -5.00R | +7.40R | FAIL ✗ |
| H-01+H-04+H-08 | GLOBAL | 8 | 33 | 0.868 | 39.4% | 1.17 | +3.40R | 10.00R | +0.103R | -5.00R | +8.40R | FAIL ✗ |
| H-02+H-05 | GLOBAL | 8 | 33 | 0.868 | 39.4% | 1.12 | +2.32R | 9.00R | +0.070R | -3.00R | +5.32R | FAIL ✗ |
| SYMBOL_SPECIFIC | SYM_SPECIFIC | 9 | 33 | 0.868 | 39.4% | 1.12 | +2.32R | 11.00R | +0.070R | -5.00R | +7.32R | FAIL ✗ |

---

## Detailed Results per Candidate

### BASELINE

Symbols (9): AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 34 | 0.895 | 4.47 | 47.1% | 1.54 | +9.72R | 8.00R | +0.286R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 18 | 33.3% | 0.90 | -1.20R |
| 2026-06 | 16 | 62.5% | 2.82 | +10.92R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 PANW | 5 | 60.0% | 2.70 | +3.40R | +0.680R |
| MSFT | 4 | 75.0% | 4.32 | +3.32R | +0.831R |
| META | 3 | 66.7% | 3.60 | +2.60R | +0.867R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| CRM | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| QQQ | 2 | 50.0% | 1.80 | +0.80R | +0.400R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 4 | 25.0% | 0.60 | -1.20R | -0.300R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** PANW  **Worst symbol:** LLY

---

### H-02+H-05+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✗]

Symbols (7): AMZN, CRM, LLY, META, NFLX, NVDA, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 29 | 0.763 | 3.82 | 34.5% | 0.95 | -1.00R | 9.00R | -0.034R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 16 | 25.0% | 0.60 | -4.80R |
| 2026-06 | 13 | 46.2% | 1.54 | +3.80R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| META | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| CRM | 5 | 40.0% | 1.20 | +0.60R | +0.120R |
| QQQ | 3 | 33.3% | 0.90 | -0.20R | -0.067R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** NVDA  **Worst symbol:** LLY

---

### H-01+H-05+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✓ | N ✗]

Symbols (7): AMZN, CRM, LLY, META, NFLX, NVDA, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 26 | 0.684 | 3.42 | 38.5% | 1.12 | +2.00R | 8.00R | +0.077R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 15 | 26.7% | 0.65 | -3.80R |
| 2026-06 | 11 | 54.5% | 2.16 | +5.80R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 META | 3 | 66.7% | 3.60 | +2.60R | +0.867R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| CRM | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| QQQ | 2 | 50.0% | 1.80 | +0.80R | +0.400R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** META  **Worst symbol:** LLY

---

### H-05+H-06+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✓ | N ✗]

Symbols (8): AMZN, CRM, LLY, META, NFLX, NVDA, QQQ, AAPL

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 26 | 0.684 | 3.42 | 38.5% | 1.12 | +2.00R | 8.00R | +0.077R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 15 | 26.7% | 0.65 | -3.80R |
| 2026-06 | 11 | 54.5% | 2.16 | +5.80R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 META | 3 | 66.7% | 3.60 | +2.60R | +0.867R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| CRM | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| QQQ | 2 | 50.0% | 1.80 | +0.80R | +0.400R |
| AAPL | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 4 | 25.0% | 0.60 | -1.20R | -0.300R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** META  **Worst symbol:** LLY

---

### H-01+H-02+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✓]

Symbols (8): AMZN, CRM, LLY, META, NFLX, NVDA, PANW, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 35 | 0.921 | 4.61 | 37.1% | 1.06 | +1.40R | 10.00R | +0.040R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 20 | 25.0% | 0.60 | -6.00R |
| 2026-06 | 15 | 53.3% | 2.06 | +7.40R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 PANW | 5 | 60.0% | 2.70 | +3.40R | +0.680R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| META | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| CRM | 5 | 40.0% | 1.20 | +0.60R | +0.120R |
| QQQ | 3 | 33.3% | 0.90 | -0.20R | -0.067R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ AMZN | 6 | 16.7% | 0.36 | -3.20R | -0.533R |

**Best symbol:** PANW  **Worst symbol:** AMZN

---

### H-02+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✓]

Symbols (8): AMZN, CRM, LLY, META, NFLX, NVDA, PANW, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 34 | 0.895 | 4.47 | 38.2% | 1.11 | +2.40R | 9.00R | +0.071R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 19 | 26.3% | 0.64 | -5.00R |
| 2026-06 | 15 | 53.3% | 2.06 | +7.40R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 PANW | 5 | 60.0% | 2.70 | +3.40R | +0.680R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| META | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| CRM | 5 | 40.0% | 1.20 | +0.60R | +0.120R |
| QQQ | 3 | 33.3% | 0.90 | -0.20R | -0.067R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** PANW  **Worst symbol:** LLY

---

### H-01+H-04+H-08

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✗]

Symbols (8): AMZN, CRM, LLY, META, NFLX, NVDA, PANW, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 33 | 0.868 | 4.34 | 39.4% | 1.17 | +3.40R | 10.00R | +0.103R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 19 | 26.3% | 0.64 | -5.00R |
| 2026-06 | 14 | 57.1% | 2.40 | +8.40R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 META | 3 | 66.7% | 3.60 | +2.60R | +0.867R |
| PANW | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| CRM | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| NVDA | 7 | 42.9% | 1.35 | +1.40R | +0.200R |
| QQQ | 2 | 50.0% | 1.80 | +0.80R | +0.400R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** META  **Worst symbol:** LLY

---

### H-02+H-05

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✗]

Symbols (8): AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, QQQ

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 33 | 0.868 | 4.34 | 39.4% | 1.12 | +2.32R | 9.00R | +0.070R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 17 | 29.4% | 0.75 | -3.00R |
| 2026-06 | 16 | 50.0% | 1.67 | +5.32R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 MSFT | 4 | 75.0% | 4.32 | +3.32R | +0.831R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| META | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| CRM | 5 | 40.0% | 1.20 | +0.60R | +0.120R |
| QQQ | 3 | 33.3% | 0.90 | -0.20R | -0.067R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** MSFT  **Worst symbol:** LLY

---

### SYMBOL_SPECIFIC

**Gate:** OOS FAIL ✗  [PF ✗ | TR ✗ | DD ✗ | N ✗]

Symbols (9): AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, QQQ, AAPL

| n | T/Day | T/Week | WR | PF | TotalR | MaxDD | AvgR |
|--:|------:|-------:|---:|---:|-------:|------:|-----:|
| 33 | 0.868 | 4.34 | 39.4% | 1.12 | +2.32R | 11.00R | +0.070R |

**Monthly breakdown:**

| Month | n | WR | PF | TotalR |
|-------|--:|---:|---:|-------:|
| 2026-05 | 19 | 26.3% | 0.64 | -5.00R |
| 2026-06 | 14 | 57.1% | 2.22 | +7.32R |

**Symbol contribution:**

| Symbol | n | WR | PF | TotalR | AvgR |
|--------|--:|---:|---:|-------:|-----:|
| 🏆 MSFT | 4 | 75.0% | 4.32 | +3.32R | +0.831R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R | +0.400R |
| META | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| CRM | 4 | 50.0% | 1.80 | +1.60R | +0.400R |
| QQQ | 3 | 33.3% | 0.90 | -0.20R | -0.067R |
| AAPL | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R | -1.000R |
| AMZN | 5 | 20.0% | 0.45 | -2.20R | -0.440R |
| ⚠ LLY | 5 | 20.0% | 0.45 | -2.20R | -0.440R |

**Best symbol:** MSFT  **Worst symbol:** LLY

---

## OOS Gate Results

Gate conditions (vs baseline OOS):
- PF > 1.54
- TotalR > +9.72R
- MaxDD ≤ 8.00R
- Trades ≥ 34

**No candidate passed all four gate conditions.**

Closest: H-01+H-05+H-08 with 1/4 conditions met.


> Next step: production recommendation requires explicit user approval.
