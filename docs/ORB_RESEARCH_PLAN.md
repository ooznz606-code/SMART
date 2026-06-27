# ORB Daily Engine — Research & Validation Plan

**Status:** Research only — no production code changes pending validation gate  
**Engine file:** `smart_analyzer_bridge_orb.py`  
**Baseline tag:** `stable-orb-pro-f2-f3-f4`

---

## Hard Rule (Production Gate)

> No parameter change, filter addition, or strategy modification may be merged into
> `smart_analyzer_bridge_orb.py` until a candidate configuration **beats the
> baseline on all four metrics simultaneously** in the walk-forward validation phase:
>
> | Metric | Requirement |
> |--------|-------------|
> | Profit Factor (PF) | Candidate PF > Baseline PF |
> | Total R | Candidate TotalR > Baseline TotalR |
> | Max Drawdown (MaxDD) | Candidate MaxDD < Baseline MaxDD |
> | Trade Count | Candidate trades ≥ Baseline trades (no filter over-tightening) |
>
> **All four must pass simultaneously. Partial improvement is not sufficient.**

---

## Design Principle

**This framework is data-driven. Hypotheses are not written before research begins.**

The first five phases are pure observation and measurement. No parameter is touched,
no direction is assumed. Hypotheses are generated only after phases 2–5 reveal what
the data actually shows. Any hypothesis written before that point is speculation, not
research, and must not influence the design of phases 2–5.

---

## Current Baseline (from `stable-orb-pro-f2-f3-f4`)

| Parameter | Value |
|-----------|-------|
| ADX minimum | 30.0 |
| RVOL minimum | 1.5× |
| ORB range minimum | 2.0 ATR |
| EMA20 distance minimum | 1.95 ATR |
| Breakout distance minimum (F3) | 0.05 ATR |
| Max same-direction signals/day (F2) | 2 |
| MSFT SHORT NEUTRAL blocked (F4) | Yes |
| Stop loss | 1.5 ATR |
| Take profit | 2.7 ATR (1.8R) |
| Max hold | 40 bars |
| Max live signals/day | 3 |
| Breakout window | 10:00–11:30 ET |
| Excluded symbols | AAPL, AMD, AVGO, COST, GOOGL, SPY, TSLA, UBER |

Baseline metrics must be computed and recorded before any candidate is evaluated.

---

## Research Phases

---

### Phase 1 — Research Architecture

**Goal:** Define and lock the research environment before any data is touched.
Nothing in phases 2–10 may proceed until this phase is complete and recorded.

**Tasks:**

1. **Metric definitions** — agree on exact formulas for PF, TotalR, MaxDD, trade count.
   Ambiguity here invalidates every downstream comparison.

2. **Backtest engine** — choose or build the replay engine.
   It must use only data available at bar close (no lookahead).
   It must match production logic in `scan_orb_live()` exactly.

3. **Data inventory** — list all available 15m history per symbol with exact date ranges.
   Record any gaps, missing days, or symbols with fewer than 6 months of data.

4. **IS / OOS split** — define the split dates once and record them here.
   These dates must not change after this phase ends.

   ```
   In-sample (IS):      [start date] → [split date]   — phases 4–8
   Out-of-sample (OOS): [split date] → [end date]     — phase 9 only (touch once)
   ```

   > The OOS window is locked after this step. It must not be examined,
   > plotted, or referenced until phase 9.

5. **Reproducibility** — define random seed policy (if any), run order, and output
   format so that any run can be reproduced from the same inputs.

6. **Output location** — all phase outputs go to `docs/results/`.
   No results are stored anywhere else.

**Deliverable:** This document updated with exact IS/OOS dates and confirmed data inventory.  
**Gate:** All six tasks above documented before phase 2 begins.

---

### Phase 2 — Allowed Symbol Audit

**Goal:** For each of the 9 ORB-scanned symbols, measure what is actually happening
in the IS data. No filtering assumptions. Pure observation.

**Symbols:** LLY, PANW, CRM, QQQ, MSFT, META, AMZN, NVDA, NFLX

**Measurements per symbol (IS window):**

| Metric | Description |
|--------|-------------|
| Days in scan window | How many trading days had bars in the 10:00–11:30 ET window |
| Bars in scan window | Total 15m bars available in the window across IS period |
| ADX distribution | Percentiles: 10th, 25th, 50th, 75th, 90th |
| RVOL distribution | Same percentiles |
| ORB range / ATR distribution | Same percentiles |
| EMA20 distance / ATR distribution | Same percentiles |
| Breakout frequency | How often price crossed ORB high or low during window |
| Breakout direction split | % LONG vs SHORT breakouts |
| Bias distribution | % days BULL / BEAR / NEUTRAL |
| Counter-bias breakout rate | How often a breakout occurred against the bias |
| Days with 0 bars in window | Missing data days |

**Per-symbol outcome (if signals were emitted under current filters):**

| Metric | Description |
|--------|-------------|
| Signals generated | Count under current parameters |
| Win rate | % of signals that hit TP1 before SL |
| Average R | Mean outcome in R units |
| PF | Per-symbol profit factor |
| MaxDD | Per-symbol max drawdown |
| Primary rejection reason | Most common filter that blocked a bar |

**Output:** `docs/results/phase2_allowed_symbol_audit.csv` (one row per symbol)  
**Gate:** All 9 symbols measured. No hypothesis written yet.

---

### Phase 3 — Excluded Symbol Audit

**Goal:** Measure the same statistics for the 8 excluded symbols under the current
filter set. This determines whether any exclusion should be revisited in phase 6.

**Symbols:** AAPL, AMD, AVGO, COST, GOOGL, SPY, TSLA, UBER

**Measurements:** Identical to Phase 2 — all distributions and outcome metrics,
computed as if each excluded symbol were in the scan list.

**Additional measurement per excluded symbol:**

| Metric | Description |
|--------|-------------|
| Exclusion rationale | Original documented reason for exclusion |
| Signal count (hypothetical) | How many signals would have fired under current filters |
| Hypothetical PF | If signals had been taken |
| Hypothetical MaxDD | If signals had been taken |
| Risk flag | Specific behavior (e.g. gap risk, illiquidity, index overlap) that motivated exclusion |

**Output:** `docs/results/phase3_excluded_symbol_audit.csv` (one row per symbol)  
**Gate:** All 8 symbols measured. No hypothesis written yet.

---

### Phase 4 — Parameter Discovery

**Goal:** For each filter parameter, measure empirically how tightening or relaxing it
affects trade count and outcome quality across the IS window. This is a sweep, not
an optimization. The purpose is to understand sensitivity, not to find the best value.

**Parameters to sweep:**

| Parameter | Current | Sweep range | Step |
|-----------|---------|-------------|------|
| ADX minimum | 30.0 | 15 → 40 | 2.5 |
| RVOL minimum | 1.5× | 0.8× → 2.5× | 0.1 |
| ORB range / ATR | 2.0 | 0.5 → 3.5 | 0.25 |
| EMA20 distance / ATR | 1.95 | 0.5 → 3.5 | 0.25 |
| Breakout distance (F3) | 0.05 | 0.01 → 0.15 | 0.01 |
| Breakout window end (ET) | 11:30 | 10:30 → 13:00 | 15 min |

**For each sweep point, record:**
- Trade count (all 9 symbols combined, IS window)
- Win rate
- PF
- TotalR
- MaxDD

**Each parameter is swept in isolation — all others held at baseline.**

This phase produces sensitivity curves, not a recommended value.
The output is read in phase 5 to measure which parameters matter most.

**Output:** `docs/results/phase4_parameter_discovery.csv`  
(columns: parameter, value, trade_count, win_rate, PF, TotalR, MaxDD)  
**Gate:** Full sweep complete for all 6 parameters. No hypothesis written yet.

---

### Phase 5 — Feature Importance

**Goal:** Rank the filters by how much of the trade-blocking and outcome variance
they explain. Identify which filters are doing real work and which are redundant
or masking other effects.

**Analyses:**

1. **Rejection share** — across all bars that failed at least one filter in the IS
   window, what % were blocked by each filter as the first failure?
   (A bar may fail multiple filters; count only the first one encountered in the
   scan order.)

2. **Incremental trade unlock** — starting from the most restrictive point of each
   sweep in phase 4, how many additional trades does each relaxation step unlock?
   Which parameter unlocks the most trades per unit of relaxation?

3. **Outcome sensitivity** — from the phase 4 curves, which parameter shows the
   steepest PF / TotalR slope around the current baseline value? High slope =
   high sensitivity = small change has large effect (good or bad).

4. **Filter interaction check** — for the two highest-rejection filters identified
   in step 1: how often do bars fail both simultaneously? If the overlap is high,
   the filters are correlated and relaxing both together may not add as much as
   relaxing each separately.

5. **Bias alignment rate** — what fraction of valid breakouts (ORB high/low crossed)
   were blocked only by counter-bias? How does this vary by symbol and by market
   condition?

**Output:** `docs/results/phase5_feature_importance.md`
(ranked filter list with rejection share, sensitivity score, and interaction flag)

**Gate:** All 5 analyses complete. Phase 5 output reviewed before phase 6 begins.

---

### Phase 6 — Hypothesis Generation

**Goal:** Write hypotheses. Only now, after phases 2–5 have been measured and read.

Each hypothesis must cite the specific phase 2–5 finding that motivates it.
A hypothesis with no citation to a measurement is not permitted.

**Template per hypothesis:**

```
ID:          H-<number>
Parameter:   <which parameter or symbol list changes>
Change:      <from X to Y — exact values>
Evidence:    <cite the specific phase 2/3/4/5 finding that motivates this>
Prediction:  <expected direction of each gate metric: PF / TotalR / MaxDD / trades>
Risk:        <what could go wrong; which market regime could make this fail>
```

**Scope constraints:**
- Maximum 10 hypotheses at this stage
- Each hypothesis changes at most 2 parameters
- No hypothesis may contradict a phase 5 finding without explaining the conflict
- Hypotheses about excluded symbols must cite phase 3 data

**Output:** `docs/results/phase6_hypotheses.md`  
**Gate:** User reviews and approves hypothesis list before phase 7 begins.

---

### Phase 7 — Hypothesis Validation (In-Sample)

**Goal:** Test each approved hypothesis individually on IS data.

**Protocol per hypothesis:**
1. Apply exactly the change described in the hypothesis
2. Run backtest on IS window only
3. Record all four gate metrics
4. Compare each metric against IS baseline
5. Assign verdict: `IS-PASS` (all 4 metrics improve) / `IS-FAIL` / `IS-MIXED`

**Output:** `docs/results/phase7_hypothesis_validation.csv`

| run_id | hypothesis_id | parameter | value | IS_PF | IS_TotalR | IS_MaxDD | IS_trades | vs_baseline | verdict |
|--------|--------------|-----------|-------|-------|-----------|----------|-----------|-------------|---------|

Only `IS-PASS` hypotheses advance to phase 8.  
`IS-MIXED` results are documented with commentary but do not advance.

**Gate:** All hypotheses tested. Results recorded.

---

### Phase 8 — Combination Validation (In-Sample)

**Goal:** Test combinations of phase 7 passing hypotheses on IS data.

**Protocol:**
- Combine only hypotheses that individually received `IS-PASS` in phase 7
- Each combination changes at most 3 parameters total
- Run each combination on IS window
- Apply the same four-metric gate
- Select at most one candidate to advance to phase 9 (the one with the best IS score
  that still passes all four gates)

**Limit:** Test at most 10 combinations. Overfitting risk increases with each
additional parameter tuned.

**Output:** `docs/results/phase8_combination_validation.csv`  
**Gate:** Single best candidate selected and documented before phase 9 begins.

---

### Phase 9 — Walk-Forward Validation (Out-of-Sample)

**Goal:** Validate the one candidate selected in phase 8 against untouched OOS data.

**Protocol:**
1. The OOS window is opened for the first time
2. Run the candidate once per walk-forward window — no re-tuning between windows
3. Apply the four-metric gate for each window independently

**Walk-forward schedule (rolling windows, 70% IS / 30% OOS each):**

```
Window 1:  IS = months 1–4,  OOS = month 5
Window 2:  IS = months 2–5,  OOS = month 6
Window 3:  IS = months 3–6,  OOS = month 7
```

**Acceptance:** Candidate must pass all four gate metrics in at least 2 of 3 windows.

> If the candidate fails: it is rejected. Return to phase 6.
> Do NOT re-tune parameters based on OOS results. That converts OOS into IS
> and invalidates the entire validation.

**Output:** `docs/results/phase9_walkforward.csv`  
**Gate:** Walk-forward complete. Result is OOS-PASS or OOS-FAIL.

---

### Phase 10 — Final Recommendation

**Goal:** Produce a written recommendation for or against merging the candidate
into production. This document is the basis for the user's explicit approval decision.

**Report must include:**

1. **Candidate summary** — exact parameter values that will change vs baseline
2. **Phase 2–3 findings** — what the symbol audits showed that motivated this candidate
3. **Phase 4–5 findings** — what the parameter sweep and feature importance showed
4. **IS results** — PF, TotalR, MaxDD, trade count vs baseline (phase 7–8)
5. **OOS results** — same four metrics for all three walk-forward windows (phase 9)
6. **Gate verdict** — explicit pass / fail for each of the four metrics
7. **Risk section** — market conditions under which the candidate may underperform
8. **Recommendation** — one of three verdicts:
   - `MERGE` — all gates pass, risk acceptable
   - `DO NOT MERGE` — one or more gates fail, or risk unacceptable
   - `CONDITIONAL` — gates pass but specific condition must be met before merge
     (condition must be stated explicitly)

**Final approval:** User must explicitly confirm the recommendation before any line of
production code is changed.

**Report location:** `docs/results/phase10_recommendation.md`

---

## Results Directory Structure

```
docs/
  ORB_RESEARCH_PLAN.md                      ← this file
  results/
    phase2_allowed_symbol_audit.csv         ← Phase 2
    phase3_excluded_symbol_audit.csv        ← Phase 3
    phase4_parameter_discovery.csv          ← Phase 4
    phase5_feature_importance.md            ← Phase 5
    phase6_hypotheses.md                    ← Phase 6 (user-approved)
    phase7_hypothesis_validation.csv        ← Phase 7
    phase8_combination_validation.csv       ← Phase 8
    phase9_walkforward.csv                  ← Phase 9
    phase10_recommendation.md               ← Phase 10
```

---

## What Must NOT Change During Research

- `smart_analyzer_bridge_orb.py` — production engine, read-only during research
- `analyzer_x2.py` — never modified
- `execution.py` — never modified
- `chart_data/` — live data, never deleted
- `.git/` and tag `stable-orb-pro-f2-f3-f4` — version history anchor

All backtest scripts run against copies of production data, never live state.

---

## Revision History

| Date | Change |
|------|--------|
| 2026-06-26 | Initial plan created (hypothesis-first workflow) |
| 2026-06-26 | Revised to data-driven 10-phase workflow; hypotheses moved to phase 6 |
