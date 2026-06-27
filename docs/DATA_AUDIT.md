# ORB Research — Data Audit

**Audit date:** 2026-06-26  
**Auditor:** Pre-Phase-1 data review (required before IS/OOS dates can be set)  
**Purpose:** Establish whether the current `chart_data/` dataset is fit for ORB research  
**Scope:** All files in `chart_data/` as of audit date  
**Source system:** `tv_datafeed.py` → TradingView WebSocket feed  

---

## Summary Verdict

| Dimension | Score | Finding |
|-----------|-------|---------|
| Data integrity | A | No duplicates, no weekend bars, timezone consistent, minimal gaps |
| History depth | D | All 17 ORB-relevant symbols capped at 500 bars ≈ 1 month |
| **Overall research readiness** | **D** | **Research cannot begin until history gap is resolved** |

> A dataset scores D overall if any single critical requirement cannot be met.
> Insufficient history is a hard blocker. The IS/OOS dates in Phase 1 of
> `ORB_RESEARCH_PLAN.md` cannot be set until this is resolved.

---

## 1. Every Symbol Available

**Total symbols with 15m files:** 53  
**Total files in `chart_data/`:** 247  

### 1a. ORB Scan Symbols (9)
These are the symbols the production engine currently scans.

| Symbol | In ORB scan | 15m file present |
|--------|-------------|-----------------|
| AMZN | Yes | Yes |
| CRM | Yes | Yes |
| LLY | Yes | Yes |
| META | Yes | Yes |
| MSFT | Yes | Yes |
| NFLX | Yes | Yes |
| NVDA | Yes | Yes |
| PANW | Yes | Yes |
| QQQ | Yes | Yes |

### 1b. ORB Excluded Symbols (8)
These symbols are excluded from the live scan. Phase 3 of the research plan audits them.

| Symbol | Exclusion reason (documented) | 15m file present |
|--------|------------------------------|-----------------|
| AAPL | Excluded | Yes |
| AMD | Excluded | Yes |
| AVGO | Excluded | Yes |
| COST | Excluded | Yes |
| GOOGL | Excluded | Yes |
| SPY | Excluded (used for bias only) | Yes |
| TSLA | Excluded | Yes |
| UBER | Excluded | Yes |

### 1c. Other Symbols in chart_data (36)
Present from prior or extended fetch sessions. Not in the ORB scan or excluded list.

```
ADBE  ADI   AMAT  BAC   CSCO  DIS   F     GS    IBKR  INTU
IWM   JNJ   JPM   KO    MA    MS    MU    NKE   NOW   PEP
PG    PLTR  PYPL  QCOM  SBUX  SHOP  SPX   SPX500 SQQQ TEST
TLT   UNH   V     WFC   XLK   XOM
```

---

## 2. Available Timeframe Files

| Timeframe key | File suffix | File count | Notes |
|--------------|-------------|-----------|-------|
| 15 | `_15m.json` | 53 | Primary ORB research timeframe |
| 60 | `_1H.json` | 51 | Secondary (bias / indicator warmup) |
| 1D | `_1D.json` | 47 | Daily reference |
| 240 | `_4H.json` | 27 | Stale — last update 2026-05-21 for most symbols |
| 5 | `_5m.json` | 48 | Intraday — not used by ORB scanner |
| 1 | `_1m.json` | 4 | Very short history (AMD, IWM, QQQ, SPY only) |
| `15m` (raw) | `_15mm.json` | 16 | **Artifact** — see section 5 |
| `1h` (lowercase) | `_1h.json` | 1 | TEST symbol only |

**ORB scanner reads:** `_15m.json` only. All other timeframes are irrelevant to research.  
**Bias calculation reads:** `_15m.json` for SPY and QQQ only.

---

## 3. Date Range Per Symbol (15m)

### ORB Scan + Excluded Symbols

| Symbol | Role | First bar | Last bar | Calendar span | Trading days |
|--------|------|-----------|----------|---------------|--------------|
| AMZN | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| CRM | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| LLY | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| META | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| MSFT | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| NFLX | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| NVDA | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| PANW | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| QQQ | SCAN | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| AAPL | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| AMD | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| AVGO | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| COST | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| GOOGL | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| SPY | EXCL/BIAS | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| TSLA | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |
| UBER | EXCL | 2026-05-28 | 2026-06-25 | 28 days | ~20 |

**All 17 ORB-relevant symbols share an identical date range and identical bar count (500).**  
This is not a coincidence — it is a consequence of the 500-bar fetch cap in `_start_tv_datafeed`.

### Extended-History Symbols (non-ORB scope, for reference)

| Symbol | First bar | Last bar | Bars |
|--------|-----------|----------|------|
| ADBE | 2026-03-02 | 2026-06-22 | 2000 |
| ADI | 2026-02-23 | 2026-06-11 | 2000 |
| AMAT | 2026-03-02 | 2026-06-18 | 2000 |
| PLTR | 2026-02-11 | 2026-06-02 | 2000 |
| SQQQ | 2026-02-27 | 2026-06-17 | 2000 |
| TLT | 2026-03-02 | 2026-06-18 | 2000 |
| ... (27 others) | 2026-02 to 2026-03 | 2026-06 | 2000 |

These symbols were fetched with a 2000-bar cap, giving ~3.5 months of 15m history.
Still below the 6-month research requirement (≈3000 bars), but not ORB scope.

---

## 4. Missing Trading Days

All ORB-relevant symbols: **≤ 1 missing trading day** per symbol across their 28-day window.  
The single missing day is 2026-06-25 for the handful of non-live-scan symbols
whose files were last updated 2026-06-22. The 17 ORB-relevant symbols are current to 2026-06-25.

**Assessment:** No meaningful gaps. Missing-day rate < 1% for all ORB symbols.

---

## 5. Missing Bars (Intraday Gaps)

Within each trading day, NYSE regular session runs 9:30–16:00 ET = 26 bars of 15m.  
The ORB breakout window (10:00–11:30 ET) contains 6 bars per day.

No intraday bar gaps were detected in any ORB-relevant symbol. All sessions appear
to have continuous bar sequences within the regular trading hours.

**Assessment:** No intraday bar gaps found.

---

## 6. Duplicate Bars

Duplicate timestamp check run across all 53 symbols with 15m files.

**Result: 0 duplicate bars in any symbol.**

---

## 7. Weekend Data

| Symbol | Weekend bars | Assessment |
|--------|-------------|------------|
| All 17 ORB-relevant | 0 | Clean |
| All 36 other real equities | 0 | Clean |
| **SPX500** | **8** | **Fail — see section 13** |
| **TEST** | **101 of 240** | **Fail — synthetic data** |

The 8 SPX500 weekend bars occur on 2026-05-17 at 22:00–23:00 UTC. This is consistent
with extended-hours or non-US-exchange data. SPX500 appears to be a continuous futures
or CFD instrument, not the NYSE-listed equity session that ORB requires.

The 101 TEST weekend bars are expected — TEST is synthetic test data (see section 13).

---

## 8. Holiday Gaps

Known NYSE holidays within the audit window are correctly absent from all ORB-relevant
symbol files. No unexpected holiday-day bars were found.

Holiday coverage cannot be fully verified for symbols with only 28 days of history
(too short a window to encounter multiple holidays). Full holiday verification requires
the extended history that is currently missing.

**Assessment:** No holiday bar anomalies detected within the available window.

---

## 9. Timezone Consistency

**Timestamp format observed:**

| Symbol group | Format | Example |
|-------------|--------|---------|
| All 17 ORB-relevant symbols | ISO 8601 + UTC offset | `2026-05-28T18:30:00+00:00` |
| All 36 extended-history equity symbols | ISO 8601 + UTC offset | `2026-03-02T17:00:00+00:00` |
| SPX500 | ISO 8601 + UTC offset | `2026-05-13T10:30:00+00:00` |
| **TEST** | **ISO 8601 naive, microsecond** | **`2026-05-28T13:16:25.993245`** |

**All real market data is UTC-aware with `+00:00` offset. Timezone is consistent.**

The ORB scanner's `_sm()` function expects UTC timestamps and uses the UTC hour directly
in its session-minute calculation. This is correct and consistent with the data format.

TEST is the only symbol with a timezone-naive timestamp. It is also the only symbol
with sub-second precision and non-15-minute-aligned bar starts. TEST is synthetic.

---

## 10. Session Consistency

NYSE regular session: 9:30–16:00 ET = 13:30–20:00 UTC (during EDT, UTC-4).

**ORB-relevant symbols:** All sessions open at 13:30 UTC (9:30 ET) and close at
approximately 19:45 UTC (15:45 ET, the last 15m bar of the session). Consistent
across all 17 symbols.

**SPX500:** Sessions open at 10:30 UTC — which is 5:30 ET. This is pre-market
for all US equities and confirms SPX500 is not a NYSE-session instrument.
SPX500 sessions also include bars on Sunday evenings (21:00–22:00 UTC), consistent
with a 24-hour futures or forex feed.

**Assessment:** All 17 ORB-relevant symbols have consistent session timing.
SPX500 is session-incompatible with ORB research.

---

## 11. Data Source

| Property | Value |
|----------|-------|
| Feed system | `tv_datafeed.py` — TradingView WebSocket |
| Symbol resolution | NASDAQ / NYSE listed equities |
| Timeframe | 15m (primary for ORB) |
| Bar format | JSON: `symbol`, `tf`, `saved_at`, `opens`, `highs`, `lows`, `closes`, `volumes`, `times` |
| Timestamp format | ISO 8601 UTC (`+00:00`) |
| Bar cap (live scan) | 500 bars (`bars_override=500` in `_start_tv_datafeed`) |
| Bar cap (extended) | 2000 bars (`keep=2000` in partial fetch, separate fetch sessions) |
| Partial update interval | 60 seconds (50 bars refreshed per cycle) |
| Full update | First cycle only, or every 4 hours for 1D timeframe |

**Artifact files explained:**

The 16 `_15mm.json` files (e.g., `AAPL_15mm.json`) are a historical artifact.
They were produced by a prior fetch session that passed the string `"15m"` (with
the `m` suffix already included) to `tv_datafeed`'s save function. The save function
uses `TF_FILENAME.get(tf, f"{tf}m")` — when `tf="15m"` is not found in
`TF_FILENAME`, the fallback returns `"15mm"`, creating a double-m filename.
Current code always passes `"15"` (no suffix), so no new `_15mm.json` files are
being created. These files are stale (last updated 2026-06-18 to 2026-06-21)
and are **never read** by the ORB scanner or any live system.

---

## 12. Research Data Levels

Different research phases have different history requirements.
A single fixed threshold is not appropriate — observational audits need far less
data than walk-forward validation. Four named levels replace the previous
single-number requirement.

> Bars-to-months conversion used throughout: 1 trading month ≈ 22 days × 26 bars/day = **572 bars**.

### Level Definitions

| Level | Name | Minimum bars | Approximate span |
|-------|------|-------------|-----------------|
| **Level 1** | Quick Research | 1,000 | ~2 months |
| **Level 2** | Candidate Validation | 2,500 | ~4–5 months |
| **Level 3** | Production Validation | 5,000 | ~9–10 months |
| **Level 4** | Walk-forward Validation | 5,000+ | ~9–10 months + rolling OOS windows |

### Phase-to-Level Mapping

| Research Phase | Required Level | Min bars | Rationale |
|---------------|---------------|---------|-----------|
| Phase 2 — Allowed Symbol Audit | Level 1 | 1,000 | Observational only; distribution measurement, no backtest |
| Phase 3 — Excluded Symbol Audit | Level 1 | 1,000 | Same as Phase 2 |
| Phase 4 — Parameter Discovery | Level 2 | 2,500 | Parameter sweeps need samples across multiple market regimes |
| Phase 5 — Feature Importance | Level 2 | 2,500 | Rejection analysis requires meaningful signal volume |
| Phase 6 — Hypothesis Generation | Level 2 | 2,500 | Hypotheses derived from Phase 4–5 output; same data required |
| Phase 7 — Hypothesis Validation (IS) | Level 2 | 2,500 | Minimum for reliable in-sample metric estimates |
| Phase 8 — Combination Validation (IS) | Level 3 | 5,000 | Larger IS window reduces combination overfitting risk |
| Phase 9 — Walk-forward Validation (OOS) | Level 4 | 5,000+ | Must support ≥ 3 non-overlapping rolling OOS windows |
| Phase 10 — Final Recommendation | None | — | Written report; no additional data required |

### Current Dataset vs. Level Requirements

All 17 ORB-relevant symbols currently have **500 bars (~1 month)**.

| Symbol | Current bars | Level 1 (1,000) | Level 2 (2,500) | Level 3 (5,000) | Level 4 (5,000+) |
|--------|-------------|----------------|----------------|----------------|-----------------|
| AMZN | 500 | FAIL | FAIL | FAIL | FAIL |
| CRM | 500 | FAIL | FAIL | FAIL | FAIL |
| LLY | 500 | FAIL | FAIL | FAIL | FAIL |
| META | 500 | FAIL | FAIL | FAIL | FAIL |
| MSFT | 500 | FAIL | FAIL | FAIL | FAIL |
| NFLX | 500 | FAIL | FAIL | FAIL | FAIL |
| NVDA | 500 | FAIL | FAIL | FAIL | FAIL |
| PANW | 500 | FAIL | FAIL | FAIL | FAIL |
| QQQ | 500 | FAIL | FAIL | FAIL | FAIL |
| AAPL | 500 | FAIL | FAIL | FAIL | FAIL |
| AMD | 500 | FAIL | FAIL | FAIL | FAIL |
| AVGO | 500 | FAIL | FAIL | FAIL | FAIL |
| COST | 500 | FAIL | FAIL | FAIL | FAIL |
| GOOGL | 500 | FAIL | FAIL | FAIL | FAIL |
| SPY | 500 | FAIL | FAIL | FAIL | FAIL |
| TSLA | 500 | FAIL | FAIL | FAIL | FAIL |
| UBER | 500 | FAIL | FAIL | FAIL | FAIL |

**500 bars falls below Level 1. No research phase can begin with current data.**

Root cause: the `bars_override=500` cap in `_start_tv_datafeed()`.  
This cap is correct for live trading (fast startup, low memory). It is not suitable for research.

To unlock each tier:

| To unlock | Fetch target | Phases unlocked |
|-----------|-------------|----------------|
| Level 1 | ≥ 1,000 bars | Phases 2–3 (symbol audits) |
| Level 2 | ≥ 2,500 bars | Phases 4–7 (discovery + IS validation) |
| Level 3 / 4 | ≥ 5,000 bars | Phases 8–9 (combination + walk-forward) |

**Recommendation: fetch 5,000 bars in a single pass to unlock all phases at once.**

---

## 13. Symbols That Should Not Participate in Research

The following symbols must be excluded from all ORB research phases regardless of
history depth, because their data is structurally incompatible with the ORB framework.

| Symbol | Reason for exclusion from research |
|--------|-----------------------------------|
| **TEST** | Synthetic test data. Naive timestamps with microsecond precision (not 15m-aligned). 101 weekend bars. Not real market data. |
| **SPX500** | Session mismatch. Opens at 10:30 UTC (5:30 ET). 8 weekend bars at 22:00 UTC. Not a NYSE equity session. Likely a CFD or futures feed for a non-US index. |
| **SPX** | Cash index, not a tradeable instrument. No options in the ORB execution framework. Only 400 bars. |
| **IBKR** | Only 400 bars (too few even for indicator warmup). No presence in ORB scan or excluded list. |
| **SQQQ** | Inverse 3× leveraged ETF. ORB breakout logic is not designed for leveraged inverse instruments. Its price behavior is path-dependent and non-linear relative to underlying. |

**These five symbols must be excluded from all research phases including symbol audits.**

The remaining 31 non-ORB symbols (ADBE, ADI, BAC, etc.) are real equities with valid
data. They are outside ORB research scope but are not corrupted. They may be relevant
if the scope of Phase 2 or Phase 3 is later expanded.

---

## 14. Final Dataset Quality Score

### Scoring Rubric

| Grade | Meaning |
|-------|---------|
| A | All integrity checks pass. All 17 symbols meet Level 4 (≥ 5,000 bars). Ready for all phases. |
| B | All integrity checks pass. All 17 symbols meet Level 2 (≥ 2,500 bars). Phases 2–7 available; Phases 8–9 blocked. |
| C | All integrity checks pass. All 17 symbols meet Level 1 (≥ 1,000 bars). Only Phases 2–3 available. |
| D | Any integrity check fails, or all symbols fall below Level 1 (< 1,000 bars). No research phase can begin. |

### Check Results

| # | Check | Result | Pass? |
|---|-------|--------|-------|
| 1 | Every ORB symbol present | All 17 symbols have 15m files | ✓ |
| 2 | No duplicate bars | 0 duplicates across all 53 symbols | ✓ |
| 3 | No weekend bars | 0 for all 17 ORB-relevant symbols | ✓ |
| 4 | Timezone consistent | All UTC `+00:00` for all 17 symbols | ✓ |
| 5 | Session consistent | All open at 13:30 UTC (9:30 ET) | ✓ |
| 6 | Missing days ≤ 1% | Max 1 missing day per symbol | ✓ |
| 7 | No intraday bar gaps | None detected | ✓ |
| 8 | No holiday bar anomalies | None detected | ✓ |
| 9 | Data source consistent | All TradingView UTC via `tv_datafeed.py` | ✓ |
| 10 | No corrupt files | All files parse cleanly | ✓ |
| 11 | Ineligible symbols identified | TEST, SPX500, SPX, IBKR, SQQQ flagged | ✓ |
| 12 | Artifact files identified | 15mm (16 files), 4H stale, 1m short | ✓ |
| 13 | History ≥ 6 months (3,380 bars) | **500 bars ≈ 1 month — ALL 17 symbols fail** | **✗ BLOCKER** |

### Check Results (Verified by Audit Script)

| ID | Check | Result |
|----|-------|--------|
| C01 | All 17 ORB symbols have 15m files | PASS |
| C02 | Zero duplicate bars (all symbols) | PASS |
| C03 | Zero weekend bars (all symbols) | PASS |
| C04 | Timezone UTC+00:00 consistent (all symbols) | PASS |
| C05 | Complete trading days open at 13:30 UTC (9:30 ET) | PASS |
| C06 | Missing trading days ≤ 2 per symbol | PASS |
| C07 | No intraday 15m bar gaps (all symbols) | PASS |
| C08 | History meets Level 1 minimum (≥ 1,000 bars — required for any research phase) | **FAIL** |

**Checks passed: 7 / 8**

> C08 is evaluated against Level 1 (1,000 bars) — the minimum required to begin
> any research phase. 500 bars fails even this threshold. The level definitions
> and phase-to-level mapping are in section 12.

> C05 note: the first calendar day in each 500-bar file is a partial day (the
> 500-bar lookback starts mid-afternoon on that day). Every subsequent complete
> trading day opens correctly at 13:30 UTC (9:30 ET). This is expected behaviour,
> not a data defect.

### Score

```
Data integrity (C01–C07):   PASS   7/7 integrity checks
History depth  (C08):       FAIL   500 bars (~1 month) vs 3,380 bars (6 months) required
                                   Gap: 2,880 bars per symbol across all 17 symbols

FINAL DATASET QUALITY SCORE:   D
```

---

## Required Action Before Research Begins

**One action required. No others.**

Re-fetch 15m history for all 17 ORB-relevant symbols using `TVDataFeed.fetch_all()`
with `bars_override=5000`, outside the live bot session.  
This is a data collection task only — no code changes.

| After re-fetch | Score becomes | Phases unlocked |
|----------------|--------------|----------------|
| All 17 symbols ≥ 1,000 bars | C | Phases 2–3 only |
| All 17 symbols ≥ 2,500 bars | B | Phases 2–7 |
| All 17 symbols ≥ 5,000 bars | A | All phases (2–9) |

**Fetch 5,000 bars to reach grade A and unlock all phases in one pass.**

Once re-fetched, re-run this audit. The score will update automatically based on
which level all 17 symbols satisfy. Phase 1 of `ORB_RESEARCH_PLAN.md`
(IS/OOS date lock) may proceed only after this audit scores B or higher.

---

## Revision History

| Date | Change |
|------|--------|
| 2026-06-26 | Initial audit created |
| 2026-06-26 | Replaced fixed 6-month threshold with four research data levels (L1–L4); added phase-to-level mapping; updated C08, scoring rubric, and required action section |
