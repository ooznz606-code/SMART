# Phase 11D — Statistical Eligibility Screening

> **Scope:** Statistical analysis only. No recommendations. No production changes.

## Eligibility Criteria

| Criterion | Definition |
|-----------|----------|
| **C1** — IS→OOS consistent | IS AvgR/T > 0 AND OOS AvgR/T > 0 AND OOS ≥ IS × 50% |
| **C2** — OOS beats baseline | OOS AvgR/T > baseline OOS AvgR/T (+0.286R) |
| **C3** — Statistical signal | OOS t-statistic > t_crit(df, α=0.10) one-tailed |
| **C4** — Minimum sample | OOS n ≥ 10 trades |
| **ELIGIBLE** | C1+C2+C3+C4 all met AND OOS n ≥ 20 |
| **TRACK_MORE_DATA** | C1+C2 met, C3 or C4 insufficient |
| **NOT_ELIGIBLE** | C1 or C2 fails |

---

**Baseline OOS reference:** 34 trades · AvgR/T +0.286R · StdR 1.396R · t = +1.19  
**OOS window:** 2026-05-01 → 2026-06-25 (2 months)  

---

## OOS Trade-Level Statistics

| Filter | n | AvgR/T | StdR | t-stat | p (1-tail) | WR | WR 90% CI | IS→OOS |
|--------|--:|-------:|-----:|-------:|-----------:|---:|----------:|:------:|
| BASELINE | 34 | +0.286R | 1.396 | +1.19 | >0.10 | 47.1% | 34%–61% | BASELINE |
| F-REG | 34 | +0.286R | 1.396 | +1.19 | >0.10 | 47.1% | 34%–61% | HOLDS |
| F-RR | 14 | +0.523R | 1.397 | +1.40 | <0.10 | 57.1% | 36%–76% | HOLDS |
| F-ORB | 9 | +1.058R | 1.219 | +2.60 | <0.025 | 77.8% | 50%–92% | HOLDS |
| F-ADX | 0 | +0.000R | — | — | >0.50 | 0.0% | — | NO_OOS_ACTIVITY |
| F-RR+REG | 14 | +0.523R | 1.397 | +1.40 | <0.10 | 57.1% | 36%–76% | HOLDS |
| F-ORB+REG | 9 | +1.058R | 1.219 | +2.60 | <0.025 | 77.8% | 50%–92% | HOLDS |
| F-RR+ORB | 6 | +1.154R | 1.140 | +2.48 | <0.05 | 83.3% | 50%–96% | HOLDS |
| F-ALL | 6 | +1.154R | 1.140 | +2.48 | <0.05 | 83.3% | 50%–96% | HOLDS |

## Sample Size Requirements (80% power, α=0.10 one-tailed)

| Filter | OOS n | n req. | Gap | T/mo | Mo needed | Verdict |
|--------|------:|-------:|----:|-----:|----------:|:-------:|
| BASELINE | 34 | 108 | 74 | 17.0 | 5 mo | REFERENCE |
| F-REG | 34 | 108 | 74 | 17.0 | 5 mo | NOT_ELIGIBLE |
| F-RR | 14 | 33 | 19 | 7.0 | 3 mo | TRACK_MORE_DATA |
| F-ORB | 9 | 6 | 0 | 4.5 | now | TRACK_MORE_DATA |
| F-ADX | 0 | — | — | 0.0 | — | NO_OOS_ACTIVITY |
| F-RR+REG | 14 | 33 | 19 | 7.0 | 3 mo | TRACK_MORE_DATA |
| F-ORB+REG | 9 | 6 | 0 | 4.5 | now | TRACK_MORE_DATA |
| F-RR+ORB | 6 | 5 | 0 | 3.0 | now | TRACK_MORE_DATA |
| F-ALL | 6 | 5 | 0 | 3.0 | now | TRACK_MORE_DATA |

## Criteria Scorecard

| Filter | C1 | C2 | C3 | C4 | Verdict |
|--------|:--:|:--:|:--:|:--:|:-------:|
| BASELINE | — | — | — | — | REFERENCE |
| F-REG | ✓ | ✗ | ✗ | ✓ | **NOT_ELIGIBLE** |
| F-RR | ✓ | ✓ | ✓ | ✓ | **TRACK_MORE_DATA** |
| F-ORB | ✓ | ✓ | ✓ | ✗ | **TRACK_MORE_DATA** |
| F-ADX | ✗ | ✗ | ✗ | ✗ | **NO_OOS_ACTIVITY** |
| F-RR+REG | ✓ | ✓ | ✓ | ✓ | **TRACK_MORE_DATA** |
| F-ORB+REG | ✓ | ✓ | ✓ | ✗ | **TRACK_MORE_DATA** |
| F-RR+ORB | ✓ | ✓ | ✓ | ✗ | **TRACK_MORE_DATA** |
| F-ALL | ✓ | ✓ | ✓ | ✗ | **TRACK_MORE_DATA** |

---

## Phase 11D Summary

**Eligible for Phase 12 (0):** none  
**Track more data (6):** `F-RR`, `F-ORB`, `F-RR+REG`, `F-ORB+REG`, `F-RR+ORB`, `F-ALL`  
**Not eligible (2):** `F-REG`, `F-ADX`  

### Per-filter notes

**F-REG** (Exclude BEAR regime days):  
OOS n=34  AvgR/T=+0.286R  p=>0.10  WR=47.1% [90%CI: 34%–61%]  n_required≈108  time_to_qualify≈5 additional months  verdict=**NOT_ELIGIBLE**  

**F-RR** (Range ratio 0.80-1.20):  
OOS n=14  AvgR/T=+0.523R  p=<0.10  WR=57.1% [90%CI: 36%–76%]  n_required≈33  time_to_qualify≈3 additional months  verdict=**TRACK_MORE_DATA**  

**F-ORB** (ORB avg ≥ 3.5 ATR):  
OOS n=9  AvgR/T=+1.058R  p=<0.025  WR=77.8% [90%CI: 50%–92%]  n_required≈6  time_to_qualify≈now  verdict=**TRACK_MORE_DATA**  

**F-ADX** (SPY daily ADX ≥ 35):  
OOS n=0  AvgR/T=+0.000R  p=>0.50  WR=0.0% [90%CI: 0%–0%]  n_required≈?  time_to_qualify≈now  verdict=**NO_OOS_ACTIVITY**  

**F-RR+REG** (Range ratio 0.80-1.20 AND not-BEAR):  
OOS n=14  AvgR/T=+0.523R  p=<0.10  WR=57.1% [90%CI: 36%–76%]  n_required≈33  time_to_qualify≈3 additional months  verdict=**TRACK_MORE_DATA**  

**F-ORB+REG** (ORB ≥ 3.5 ATR AND not-BEAR):  
OOS n=9  AvgR/T=+1.058R  p=<0.025  WR=77.8% [90%CI: 50%–92%]  n_required≈6  time_to_qualify≈now  verdict=**TRACK_MORE_DATA**  

**F-RR+ORB** (Range ratio 0.80-1.20 AND ORB ≥ 3.5):  
OOS n=6  AvgR/T=+1.154R  p=<0.05  WR=83.3% [90%CI: 50%–96%]  n_required≈5  time_to_qualify≈now  verdict=**TRACK_MORE_DATA**  

**F-ALL** (Not-BEAR AND RR 0.80-1.20 AND ORB ≥ 3.5):  
OOS n=6  AvgR/T=+1.154R  p=<0.05  WR=83.3% [90%CI: 50%–96%]  n_required≈5  time_to_qualify≈now  verdict=**TRACK_MORE_DATA**  

> No final recommendation written. No parameters changed. Phase 12 requires explicit approval.
