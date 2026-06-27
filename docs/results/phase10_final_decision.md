# Phase 10 — Final Research Decision

**Research scope:** SMART ORB System — IS/OOS Validation Study  
**IS window:** 2025-09-17 → 2026-04-30 (156 trading days)  
**OOS window:** 2026-05-01 → 2026-06-25 (38 trading days)  
**Decision date:** 2026-06-26  
**Status:** CLOSED — NO PRODUCTION CHANGE

---

## 1. Research Journey Summary

| Phase | Purpose | Output |
|-------|---------|--------|
| 0D | Research scope lock | `docs/RESEARCH_SCOPE.md` |
| 1–4 | Baseline validation, filter analysis | 100-signal baseline confirmed: PF 1.73, +35.98R, MaxDD 11.60R |
| 5 | Feature importance | ADX and RVOL most restrictive; F3 inert |
| 6 | Hypothesis generation | 8 hypotheses; 6 approved, 1 conditional, 1 negative |
| 7 | IS validation (individual + combinations) | 29 PASS / 13 FAIL across 42 configs |
| 7B | Robustness check (monthly) | 17 ROBUST, 10 CONDITIONAL, 0 FRAGILE; 7 OOS candidates |
| 8 | Portfolio expansion (diversification metrics) | Best: H-02+H-05+H-08, PF 2.57, A+ grade |
| 8B | Symbol-level optimization | PANW: REMOVE; MSFT: KEEP (optimized); AAPL: add candidate |
| 9 | OOS validation | All 8 candidates FAIL; baseline outperforms all |
| **10** | **Final decision** | **NO CHANGE** |

---

## 2. IS Validation Results (Summary)

**Baseline (locked reference):** 100 trades · WR 49.0% · PF 1.73 · TotalR +35.98R · MaxDD 11.60R

Top IS candidates that advanced to OOS gate (Phase 7B ROBUST + PF ≥ 2.00 + TotalR ≥ +45R + MaxDD ≤ 10.0R):

| Candidate | Trades | PF | TotalR | MaxDD | Win Months | Worst Month |
|-----------|-------:|---:|-------:|------:|:----------:|------------:|
| H-02+H-05+H-08 | 82 | 2.57 | +51.18R | 6.60R | 7/8 | -1.00R |
| H-01+H-05+H-08 | 83 | 2.41 | +47.96R | 7.37R | 7/8 | -1.77R |
| H-05+H-06+H-08 | 81 | 2.36 | +45.67R | 7.60R | 7/8 | -2.00R |
| H-01+H-02+H-08 | 101 | 2.28 | +55.16R | 9.37R | 7/8 | -2.77R |
| H-02+H-08 | 91 | 2.21 | +47.78R | 8.60R | 7/8 | -2.00R |
| H-01+H-04+H-08 | 101 | 2.02 | +46.76R | 9.37R | 6/8 | -2.77R |
| H-02+H-05 | 100 | 2.12 | +49.98R | 9.60R | 7/8 | -2.00R |

IS performance looked compelling. IS implied removing MSFT and PANW was beneficial. IS implied relaxing ORB_RANGE and RVOL improved robustness.

---

## 3. OOS Validation Results (Summary)

**OOS Baseline:** 34 trades · WR 47.1% · PF 1.54 · TotalR +9.72R · MaxDD 8.00R

**Gate:** candidate must beat baseline on all four: PF · TotalR · MaxDD · trade count

| Candidate | n | PF | TotalR | MaxDD | Gate |
|-----------|--:|---:|-------:|------:|:----:|
| BASELINE | 34 | 1.54 | +9.72R | 8.00R | — |
| H-02+H-05+H-08 | 29 | 0.95 | -1.00R | 9.00R | **FAIL** |
| H-01+H-05+H-08 | 26 | 1.12 | +2.00R | 8.00R | **FAIL** |
| H-05+H-06+H-08 | 26 | 1.12 | +2.00R | 8.00R | **FAIL** |
| H-01+H-02+H-08 | 35 | 1.06 | +1.40R | 10.00R | **FAIL** |
| H-02+H-08 | 34 | 1.11 | +2.40R | 9.00R | **FAIL** |
| H-01+H-04+H-08 | 33 | 1.17 | +3.40R | 10.00R | **FAIL** |
| H-02+H-05 | 33 | 1.12 | +2.32R | 9.00R | **FAIL** |
| SYMBOL_SPECIFIC | 33 | 1.12 | +2.32R | 11.00R | **FAIL** |

**No candidate passed. Zero OOS winners.**

---

## 4. OOS Baseline Symbol Contribution

| Symbol | n | WR | PF | TotalR |
|--------|--:|---:|---:|-------:|
| PANW | 5 | 60.0% | 2.70 | +3.40R |
| MSFT | 4 | 75.0% | 4.32 | +3.32R |
| META | 3 | 66.7% | 3.60 | +2.60R |
| NVDA | 6 | 50.0% | 1.80 | +2.40R |
| CRM | 4 | 50.0% | 1.80 | +1.60R |
| QQQ | 2 | 50.0% | 1.80 | +0.80R |
| NFLX | 1 | 0.0% | 0.00 | -1.00R |
| AMZN | 4 | 25.0% | 0.60 | -1.20R |
| LLY | 5 | 20.0% | 0.45 | -2.20R |

**PANW was the best OOS symbol (+3.40R).** Every candidate that removed it (H-05/H-08 series) discarded the single largest OOS contributor.

**MSFT was the second best OOS symbol (+3.32R, WR 75%).** Its IS removal (H-08) appeared justified in IS but was costly in OOS.

**LLY was consistently worst (-2.20R, WR 20%) in every portfolio.** It was never removed by any hypothesis, yet it dragged all portfolios equally.

---

## 5. Why the IS Optimization Failed OOS

### 5a. Symbol removal reversed in OOS

| Symbol | IS verdict | IS TotalR | OOS TotalR | OOS verdict |
|--------|-----------|----------:|----------:|------------|
| PANW | REMOVE (Grade D) | -3.40R | +3.40R | Best OOS symbol |
| MSFT | REMOVE (H-08) | -0.20R (BL) | +3.32R | 2nd best OOS symbol |

Both symbols were weak in IS under BL params and performed strongly in OOS. This is a regime shift: the market conditions that made PANW and MSFT weak during the IS period did not persist into OOS.

### 5b. Parameter relaxations amplified a losing month

All parameter relaxations (H-01: RVOL 1.5→1.4, H-02: ORB_RANGE 2.0→1.0, H-04: session extension) increased trade count. In May 2026 (a losing month), more trades meant more losses. Tighter baseline filters generated fewer May trades and landed on better-quality setups.

| Month | Baseline | Best candidate (H-01+H-04+H-08) |
|-------|:--------:|:-------------------------------:|
| May 2026 | -1.20R (18 trades) | -5.00R (19 trades) |
| Jun 2026 | +10.92R (16 trades) | +8.40R (14 trades) |

The baseline won May by doing less. It won June by keeping PANW and MSFT.

### 5c. The IS win rate did not transfer

IS WR for top candidates: 55–58%. OOS WR: 34–39%. The IS period (Sep 2025 – Apr 2026) appears to have been a favorable trending regime for the relaxed ORB parameters. OOS (May–Jun 2026) showed mean-reversion or choppier price action where the tighter baseline filters were more protective.

### 5d. 38 OOS days is a small sample

OOS inference is limited. With 38 trading days and 26–35 trades per candidate, single-month variance (May's -1.20R swing to -6.00R) dominates the result. This cannot definitively prove the IS optimization was overfit — it may simply reflect regime timing. **This is the core reason Phase 11 is recommended rather than treating these results as conclusive.**

---

## 6. Production Decisions

### 6.1 What is NOT permitted

The following changes are blocked. No production code, configuration, or live system parameter may be altered as a result of this research cycle:

- No ORB production parameter changes of any kind
- No symbol removal (PANW and MSFT remain in the live scan)
- No AAPL addition to the live scan
- No symbol-specific settings or per-symbol parameter overrides
- No ORB_RANGE change (stays at 2.0)
- No RVOL change (stays at 1.5)
- No session extension (sess_brk_end_et stays at 120, i.e., 11:30 ET)
- No ADX change
- No modification to `analyzer_x2.py`
- No replacement of `smart_analyzer_bridge_bc.py`

**Rationale:** The OOS gate was not cleared by any candidate. The scientific standard for production change requires OOS passage. It was not achieved.

### 6.2 What is permitted

The following are explicitly allowed and do not require further approval:

- Keep all research scripts (`phase5_importance.py` through `phase9_oos_validation.py`, `phase8b_symbol_optimization.py`)
- Keep `chart_data_research/` directory and all 17 research data files untouched
- Fix and document the DST-safe research clock (`_sm_et` pattern using `zoneinfo`) in research scripts
- Continue monitoring OOS forward data as new months accumulate
- Collect additional OOS months (July 2026, August 2026, etc.) into `chart_data_research/` for future re-validation
- Begin scoping Phase 11 (no execution without approval)

---

## 7. Hypotheses Disposition

| Hypothesis | Description | IS result | OOS finding | Final status |
|-----------|-------------|-----------|------------|--------------|
| H-01 | RVOL_MIN 1.5→1.4 | PASS | Added trades in bad regime | **NOT ADOPTED** |
| H-02 | ORB_RANGE 2.0→1.0 | PASS | Added trades in bad regime | **NOT ADOPTED** |
| H-03 | ADX_MIN 30→35 | CONDITIONAL | Not tested OOS | **NOT ADOPTED** |
| H-04 | SESS_BRK_END 11:30→12:00 | PASS | Marginal OOS | **NOT ADOPTED** |
| H-05 | Remove PANW | PASS | PANW best OOS symbol | **NOT ADOPTED** |
| H-06 | Add AAPL | CONDITIONAL | Underperformed in OOS | **NOT ADOPTED** |
| H-07 | BREAK_DIST stays 0.05 | Confirmed negative | — | **CONFIRMED UNCHANGED** |
| H-08 | Remove MSFT | PASS | MSFT 2nd best OOS | **NOT ADOPTED** |

All 6 approved IS hypotheses fail to produce OOS improvement. H-07 (no-change) was correct and remains confirmed.

---

## 8. Next Research Recommendation — Phase 11

**Phase 11: Regime-Conditioned ORB Research**

### Motivation

May 2026 was a losing month across all portfolios (+0 to -6R). June 2026 was strongly positive. This pattern — volatile or mean-reverting market vs trending market — was not captured by any static parameter change in Phases 1–9.

The fundamental problem is not which parameters are set, but **when the ORB strategy should be active at all.** Static ORB parameters assume the strategy works equally in all market regimes. The OOS evidence suggests it does not.

### Research objective

Classify each trading day into a market regime before applying ORB rules. Candidate regime signals:

- **VIX level** (above/below threshold → vol regime)
- **SPY/QQQ trend strength** (ADX of daily bars, not 15m)
- **Gap size** (large overnight gap → regime signal)
- **Prior-day range** (ATR ratio to recent avg → expansion vs compression)
- **Sector breadth** (% of symbols trending above EMA)

### Research design

1. Collect regime features for each IS + OOS trading day
2. Backtest ORB signals split by regime category (low-vol trending, high-vol choppy, neutral)
3. Compare: PF/TotalR within each regime vs pooled
4. Identify whether a simple regime filter (e.g., "only trade ORB when SPY daily ADX > 20") improves OOS consistency
5. Validate any regime filter on the OOS period before proposing production changes

### Scope lock for Phase 11

- Research only — no production changes
- Use `chart_data_research/` plus new daily bar data files in a separate `chart_data_daily/` directory
- IS window extended as more data accumulates (target 9+ OOS months before next OOS test)
- Approval required before Phase 12 (any production change)

---

## 9. Production Action

**Production action: NO CHANGE.**

---

*Phase 10 closes the current research cycle. The production ORB system continues with its current parameters, symbol list, and logic unchanged. All research artifacts are preserved for Phase 11.*
