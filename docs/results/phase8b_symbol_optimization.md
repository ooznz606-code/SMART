# Phase 8B — Symbol-Level Optimization

**IS window:** 2025-09-17 → 2026-04-30  
**Symbols tested:** 10 (AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ, AAPL)  
**Parameter sets:** 8 (all subsets of H-01, H-02, H-04)  

## Grade Legend

| Grade | Meaning |
|-------|--------|
| A | Already optimal — global settings work well, no customization needed |
| B | Small improvement — global settings acceptable, optimization gives marginal gain |
| C | Significant improvement — symbol viable only with custom settings |
| D | Unsuitable — global settings insufficient, optimization does not help enough |

> **KEEP (optimized):** symbol becomes profitable after optimization → do NOT remove
> **REMOVE:** symbol remains weak after optimization

---

## Results Summary

| Symbol | BL_n | BL_PF | BL_TR | BL_DD | Best Config | Best_n | Best_PF | Best_TR | Best_DD | ΔPF | ΔTR | Grade | Recommendation |
|--------|-----:|------:|------:|------:|-------------|-------:|--------:|--------:|--------:|----:|----:|:-----:|----------------|
| AMZN✓ | 14 | 1.54 | +3.79R | 2.00R | H-01 | 15 | 1.80 | +5.59R | 2.00R | +0.26 | +1.80R | B | KEEP (optimized) |
| CRM✓ | 12 | 4.00 | +10.80R | 1.00R | H-01+H-04 | 14 | 3.70 | +11.83R | 1.37R | -0.29 | +1.03R | A | KEEP (unchanged) |
| LLY✓ | 6 | 1.80 | +2.40R | 3.00R | H-01+H-02 | 9 | 3.60 | +7.80R | 3.00R | +1.80 | +5.40R | **C** | KEEP (optimized) |
| META✓ | 11 | 4.49 | +10.46R | 1.00R | H-02 | 13 | 5.69 | +14.06R | 1.00R | +1.20 | +3.60R | **C** | KEEP (optimized) |
| MSFT✓ | 17 | 0.98 | -0.20R | 5.20R | H-01+H-04 | 18 | 1.44 | +4.40R | 5.20R | +0.46 | +4.60R | **C** | KEEP (optimized) |
| NFLX✓ | 8 | 2.44 | +4.33R | 2.00R | H-01+H-04 | 12 | 3.18 | +8.73R | 3.00R | +0.74 | +4.40R | **C** | KEEP (optimized) |
| NVDA✓ | 14 | 1.80 | +5.60R | 3.00R | H-02+H-04 | 17 | 2.57 | +11.00R | 2.00R | +0.77 | +5.40R | **C** | KEEP (optimized) |
| PANW✓ | 9 | 0.51 | -3.40R | 3.40R | BL | 9 | 0.51 | -3.40R | 3.40R | +0.00 | +0.00R | **D** | **REMOVE** |
| QQQ✓ | 9 | 1.44 | +2.20R | 5.00R | H-01+H-02 | 11 | 2.16 | +5.80R | 5.00R | +0.72 | +3.60R | **C** | KEEP (optimized) |
| AAPL+ | 7 | 4.05 | +6.10R | 1.00R | H-01+H-02+H-04 | 9 | 5.85 | +9.70R | 1.00R | +1.80 | +3.60R | **C** | KEEP (optimized) |

✓ = current BL_SYMS  + = candidate for addition

---

## Parameter Sweep by Symbol (TotalR, [best config marked])

| Symbol | BL | H-01 | H-02 | H-04 | H-01+H-02 | H-01+H-04 | H-02+H-04 | H-01+H-02+H-04 |
|--------|----:|-----:|-----:|-----:|----------:|----------:|----------:|---------------:|
| AMZN | +3.79R(14)|**+5.59R(15)**|+2.79R(15)|+3.79R(14)|+4.59R(16)|+5.59R(15)|+2.79R(15)|+4.59R(16) |
| CRM | +10.80R(12)|+10.03R(13)|+10.80R(12)|+10.80R(12)|+10.03R(13)|**+11.83R(14)**|+10.80R(12)|+11.83R(14) |
| LLY | +2.40R(6)|+4.20R(7)|+6.00R(8)|+0.40R(8)|**+7.80R(9)**|+2.20R(9)|+4.00R(10)|+5.80R(11) |
| META | +10.46R(11)|+10.46R(11)|**+14.06R(13)**|+11.26R(13)|+14.06R(13)|+11.26R(13)|+13.06R(14)|+13.06R(14) |
| MSFT | -0.20R(17)|+2.60R(17)|-1.20R(18)|+1.60R(18)|+1.60R(18)|**+4.40R(18)**|+0.60R(19)|+3.40R(19) |
| NFLX | +4.33R(8)|+6.13R(9)|+4.33R(8)|+6.93R(11)|+6.13R(9)|**+8.73R(12)**|+6.93R(11)|+8.73R(12) |
| NVDA | +5.60R(14)|+7.55R(18)|+9.20R(16)|+7.40R(15)|+10.15R(21)|+7.55R(18)|**+11.00R(17)**|+10.15R(21) |
| PANW | **-3.40R(9)**|-3.40R(9)|-3.40R(9)|-3.40R(9)|-3.40R(9)|-3.40R(9)|-3.40R(9)|-3.40R(9) |
| QQQ | +2.20R(9)|+4.00R(10)|+4.00R(10)|+1.20R(10)|**+5.80R(11)**|+3.00R(11)|+1.00R(13)|+1.80R(15) |
| AAPL | +6.10R(7)|+6.10R(7)|+7.90R(8)|+6.10R(7)|+7.90R(8)|+7.90R(8)|+7.90R(8)|**+9.70R(9)** |

---

## Grade Breakdown

### A — already optimal (no change needed)

- **CRM**: BL → PF 4.00, TotalR +10.80R  Best (H-01+H-04) → PF 3.70, TotalR +11.83R  → **KEEP (unchanged)**

### B — small improvement available

- **AMZN**: BL → PF 1.54, TotalR +3.79R  Best (H-01) → PF 1.80, TotalR +5.59R  → **KEEP (optimized)**

### C — significant improvement from custom settings

- **LLY**: BL → PF 1.80, TotalR +2.40R  Best (H-01+H-02) → PF 3.60, TotalR +7.80R  → **KEEP (optimized)**
- **META**: BL → PF 4.49, TotalR +10.46R  Best (H-02) → PF 5.69, TotalR +14.06R  → **KEEP (optimized)**
- **MSFT**: BL → PF 0.98, TotalR -0.20R  Best (H-01+H-04) → PF 1.44, TotalR +4.40R  → **KEEP (optimized)**
- **NFLX**: BL → PF 2.44, TotalR +4.33R  Best (H-01+H-04) → PF 3.18, TotalR +8.73R  → **KEEP (optimized)**
- **NVDA**: BL → PF 1.80, TotalR +5.60R  Best (H-02+H-04) → PF 2.57, TotalR +11.00R  → **KEEP (optimized)**
- **QQQ**: BL → PF 1.44, TotalR +2.20R  Best (H-01+H-02) → PF 2.16, TotalR +5.80R  → **KEEP (optimized)**
- **AAPL**: BL → PF 4.05, TotalR +6.10R  Best (H-01+H-02+H-04) → PF 5.85, TotalR +9.70R  → **KEEP (optimized)**

### D — current global settings unsuitable

- **PANW**: BL → PF 0.51, TotalR -3.40R  → **REMOVE**

---

## Estimated Portfolio Improvement

Per-symbol TotalR sum (KEEP symbols only, excluding REMOVE):

| | Symbols | Sum TotalR |
|---|---|---:|
| Global BL params | AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, QQQ, AAPL | +45.47R |
| Per-symbol optimal | AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, QQQ, AAPL | +78.90R |
| **Estimated delta** | — | **+33.43R** |

> *Per-symbol sum only. Portfolio-level MaxDD and signal ordering effects not captured.*

---

**Next step:** OOS validation requires explicit user approval.
