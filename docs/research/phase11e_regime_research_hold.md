# Phase 11E — Regime Research Hold

**Date:** 2026-06-26  
**Status:** ON HOLD — awaiting additional OOS data  
**Production action: NO CHANGE.**

---

## 1. Phase 11D Result

Statistical eligibility screening (Phase 11D) found **no filter eligible for Phase 12 production review**.

| Filter | OOS n | p (1-tail) | IS→OOS | C2 beats base | Verdict |
|--------|------:|:----------:|:------:|:-------------:|:-------:|
| F-ORB | 9 | <0.025 | HOLDS | YES | TRACK_MORE_DATA |
| F-ORB+REG | 9 | <0.025 | HOLDS | YES | TRACK_MORE_DATA |
| F-RR+ORB | 6 | <0.05 | HOLDS | YES | TRACK_MORE_DATA |
| F-ALL | 6 | <0.05 | HOLDS | YES | TRACK_MORE_DATA |
| F-RR | 14 | <0.10 | HOLDS | YES | TRACK_MORE_DATA |
| F-RR+REG | 14 | <0.10 | HOLDS | YES | TRACK_MORE_DATA |
| F-REG | 34 | >0.10 | HOLDS | NO | NOT_ELIGIBLE |
| F-ADX | 0 | — | — | — | NO_OOS_ACTIVITY |

**F-ORB is the closest to eligible.** It is the strongest signal by t-statistic (t=+2.60, p<0.025), has already exceeded the 80%-power required sample size (n_req≈6, n_have=9), and is blocked only by the minimum n≥20 OOS threshold.

**F-RR and F-RR+REG need more data.** Statistical signal is present (p<0.10) but required n≈33; current n=14. Approximately 3 additional OOS months at ~7 trades/month are needed.

**F-REG is not useful.** The OOS window (May–June 2026) contained zero BEAR-regime days. The filter removed nothing, producing performance identical to the unfiltered baseline (+0.286R/trade). It cannot be evaluated until BEAR-regime days accumulate in OOS.

**F-ADX is rejected.** The ADX ≥ 35 filter produced zero OOS trades across the full 38-day OOS window. No inference is possible.

---

## 2. Decision

- **No production change.**
- **Do not create Phase 12 now.**
- Continue collecting forward OOS data as each month closes.
- Re-run Phase 11D when sufficient data has accumulated (see Section 3).

---

## 3. Data Requirement Before Next Review

Add **July 2026 data first** as the minimum re-evaluation trigger.

Prefer **3 additional months** (July, August, September 2026) before re-running Phase 11D, to give the filters enough new trades for reliable inference.

**Minimum OOS trade targets before re-evaluation:**

| Filter group | Current OOS n | Target OOS n | Approx months needed |
|--------------|-------------:|-------------:|---------------------:|
| F-ORB-type (F-ORB, F-ORB+REG, F-RR+ORB, F-ALL) | 6–9 | ≥ 20 | ~3 months |
| F-RR-type (F-RR, F-RR+REG) | 14 | ≥ 33 | ~3 months |

Re-run sequence when data is available:
1. Fetch new monthly bars into `chart_data_research/`
2. Re-run `phase11_regime_database.py` to extend the regime DB
3. Re-run `phase11d_statistical_eligibility.py` to update verdicts
4. Assess whether any filter crosses into ELIGIBLE

---

## 4. Watchlist

The following filters passed C1 (IS→OOS consistent) and C2 (beats baseline) in Phase 11D. They remain active candidates pending more OOS data.

| Filter | Condition | OOS AvgR/T | OOS WR | p (current) | Priority |
|--------|-----------|:----------:|:------:|:-----------:|:--------:|
| **F-ORB** | ORB avg ≥ 3.5 ATR | +1.058R | 77.8% | <0.025 | High |
| **F-ORB+REG** | ORB ≥ 3.5 ATR AND not-BEAR | +1.058R | 77.8% | <0.025 | High |
| **F-RR+ORB** | Range ratio 0.80–1.20 AND ORB ≥ 3.5 ATR | +1.154R | 83.3% | <0.05 | High |
| **F-ALL** | Not-BEAR AND RR 0.80–1.20 AND ORB ≥ 3.5 ATR | +1.154R | 83.3% | <0.05 | High |
| **F-RR** | Range ratio 0.80–1.20 | +0.523R | 57.1% | <0.10 | Medium |
| **F-RR+REG** | Range ratio 0.80–1.20 AND not-BEAR | +0.523R | 57.1% | <0.10 | Medium |

Filters **not** on the watchlist (do not re-evaluate without new information):
- F-REG: requires BEAR days in OOS before meaningful evaluation
- F-ADX: zero OOS activity; re-evaluate only if market regime shifts to sustained strong-trend environment

---

## 5. Research Artifacts

All Phase 11 research is preserved. No files to be deleted or modified.

| File | Description |
|------|-------------|
| `phase11_regime_database.py` | Builds per-day regime feature database |
| `phase11b_regime_analysis.py` | Raw regime group analysis |
| `phase11c_regime_filter_test.py` | IS+OOS backtest per filter |
| `phase11d_statistical_eligibility.py` | Statistical screening — re-run with new data |
| `docs/research/phase11_regime_database.csv` | 194-row regime DB (extend with new months) |
| `docs/research/phase11b_regime_analysis.md` | Phase 11B output |
| `docs/research/phase11c_regime_filter_test.csv` / `.md` | Phase 11C output |
| `docs/research/phase11d_statistical_eligibility.md` | Phase 11D output |

---

**Production action: NO CHANGE.**
