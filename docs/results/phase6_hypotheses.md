# Phase 6 — Hypothesis Generation

**Status:** PARTIALLY APPROVED (Phase 6B) — 6 approved · 1 conditional · 1 no-change confirmed  
**IS window:** 2025-09-17 → 2026-04-30 (156 trading days)  
**Baseline:** 100 signals · PF 1.73 · TotalR +35.98R · MaxDD 7.80R  
**Constraint:** Each hypothesis cites a specific Phase 2–5 finding. Max 2 parameters per hypothesis.

---

## Design Principle

Hypotheses are written here only because Phases 2–5 have been completed and reviewed.
No hypothesis was written before those phases. Each entry below cites the specific
measurement that motivates it. A hypothesis without a citation is not permitted.

---

## Hypotheses

---

### H-01 — Relax RVOL_MIN from 1.5 to 1.4

```
ID:         H-01
Parameter:  ORB_RVOL_MIN
Change:     1.5 → 1.4
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 4 (rvol_min sweep): RVOL=1.4 is the only point in the entire Phase 4 sweep where
relaxing a filter improves ALL four gate metrics simultaneously vs baseline:
- PF:     1.73 → 1.92  (+0.19)
- TotalR: +35.98R → +47.16R  (+11.18R)
- MaxDD:  7.80R → 5.20R  (−2.60R)
- Signals: 100 → 109  (+9)

Phase 5 (rejection share): RVOL is the second-largest rejection filter at 36.0% of all
breakout-window bars (2,956 bars). Phase 5 (PF slope): the slope at baseline is a local
trough — both 1.4 and 1.7 outperform 1.5 on PF, confirming 1.5 is not the optimal point.
Phase 5 (interaction): 80.5% of ADX failures also fail RVOL, meaning RVOL is largely
redundant with ADX. The 9 signals unlocked by relaxing to 1.4 passed ADX filtering and
are therefore not low-momentum bars.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | 1.4 produced PF 1.92 in Phase 4 IS run |
| TotalR  | ↑         | +11.18R observed in Phase 4 |
| MaxDD   | ↓         | 7.80R → 5.20R observed in Phase 4 |
| Trades  | ↑         | +9 signals across IS window |

**Risk:**
The 9 unlocked signals cluster in low-RVOL conditions that may underperform in
fast-trending markets not well represented in the IS window. RVOL=1.4 vs 1.5
is a small absolute difference; OOS regime shifts (e.g. low-volatility compression)
could neutralise the IS gain.

---

### H-02 — Relax ORB_RANGE_ATR_MIN from 2.0 to 1.0

```
ID:         H-02
Parameter:  ORB_RANGE_ATR_MIN
Change:     2.0 → 1.0
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 4 (orb_range_min sweep): relaxing to 1.0 adds +9 signals and +10.60R TotalR
with equal or better PF (1.90 vs 1.73 baseline) and lower MaxDD (6.00R vs 7.80R).
Values 0.8 and 1.0 produce identical results, establishing 1.0 as the effective floor
(no additional signals exist below 1.0 ATR range in the IS data).

Phase 5 (rejection share): orb_range is the lowest first-fail filter of all threshold
filters at 0.5% of breakout-window bars (41 bars). The 9 signals unlocked by relaxing
to 1.0 passed all other filters including ADX ≥ 30 and RVOL ≥ 1.5 — they are
quality-screened breakouts on days with a tighter ORB.

Phase 4 (PF slope): shape is RISING (tighter=better), meaning the current value of 2.0
is under-performing relative to where the curve is heading. The filter is set too high
relative to its discriminating power in this dataset.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | 1.90 observed at 1.0 in Phase 4 (vs 1.73 baseline) |
| TotalR  | ↑         | +10.60R gain observed in Phase 4 |
| MaxDD   | ↓         | 6.00R vs 7.80R at baseline in Phase 4 |
| Trades  | ↑         | +9 signals; small but quality-screened |

**Risk:**
A smaller ORB range may indicate a compressed pre-breakout period rather than
a genuine directional setup. In high-noise regimes (choppy open, news-driven gaps)
tighter ORB ranges may produce more fakeouts. The OOS window contains different
market conditions and the 9 additional IS signals are a small sample.

---

### H-03 — Tighten ADX_MIN from 30 to 35

```
ID:         H-03
Parameter:  ORB_ADX_MIN
Change:     30 → 35
Approval:   CONDITIONAL — risk-control variant only
            Not a performance-improvement candidate.
            Test in Phase 7 as a risk-control check; do not treat PF gain
            as the primary goal. TotalR reduction and signal loss are
            known costs the user has acknowledged.
```

**Evidence:**
Phase 4 (adx_min sweep): ADX=35 produces the highest PF in the entire Phase 4 sweep
across all seven parameters (PF 2.10 vs baseline 1.73, +0.37).

Phase 5 (rejection share): ADX is the single largest rejection filter at 56.9% of all
breakout-window bars (4,671 bars). A filter that blocks 56.9% of bars and whose
tightening consistently improves signal quality is the primary quality gate.

Phase 5 (ADX-RVOL interaction): 80.5% of ADX failures also fail RVOL. This means
tightening ADX does NOT duplicate RVOL's work — it targets bars that pass RVOL but
lack trend strength, a distinct quality dimension.

Phase 5 (PF slope): shape at baseline is VALLEY — both loosening and tightening
improve PF relative to the baseline value of 30. This confirms 30 is a local PF minimum.

**Trade-off acknowledged:** ADX=35 reduces TotalR from +35.98R to +32.58R (−3.40R)
and signal count from 100 to 67 (−33%). The PF gain is purchased at the cost of
signal volume. This trade-off must hold in OOS to justify the change.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | 2.10 observed at ADX=35 in Phase 4 (+0.37) |
| TotalR  | ↓         | +32.58R vs +35.98R baseline (−3.40R cost) |
| MaxDD   | ↓         | 6.40R vs 7.80R baseline |
| Trades  | ↓         | 67 vs 100 baseline (−33%) |

**Risk:**
Fewer signals increases variance of the PF estimate. In a weak-trend OOS period
(sustained low-ADX environment), the system may fire very rarely, making OOS
evaluation statistically thin. The −3.40R TotalR cost may matter more than the
PF improvement in absolute return terms.

---

### H-04 — Extend SESS_BRK_END from 11:30 ET to 12:00 ET

```
ID:         H-04
Parameter:  SESS_BRK_END  (research constant: sess_brk_end_et)
Change:     120 → 150  (11:30 ET → 12:00 ET in _sm_et units)
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 4 (sess_brk_end sweep): extending to 12:00 ET adds +10 signals and +4.00R TotalR
with equal PF (1.73 vs 1.73 baseline) and marginally lower MaxDD (7.60R vs 7.80R).
The extended window adds signals without degrading quality — the cheapest improvement
found in Phase 4.

Phase 2C (signal timing): signals are currently capped at 11:30 ET. Phase 4 confirms
that the 10 additional signals occurring between 11:30 and 12:00 ET pass all filters
(ADX ≥ 30, RVOL ≥ 1.5, etc.) and produce outcomes equivalent to the baseline signal set.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | →         | Equal PF observed in Phase 4 (1.73) |
| TotalR  | ↑         | +4.00R observed in Phase 4 |
| MaxDD   | →         | 7.60R vs 7.80R (marginal improvement) |
| Trades  | ↑         | +10 signals |

**Risk:**
The 11:30–12:00 ET window coincides with post-morning-session drift and pre-lunch
liquidity thinning. In OOS periods with different intraday structure, these late-morning
signals may exhibit lower follow-through than the IS sample suggests. The +4R gain is
small relative to baseline TotalR (+11%) and may not survive OOS regime variation.

---

### H-05 — Remove PANW from scan symbols

```
ID:         H-05
Parameter:  ORB scan symbol list
Change:     Remove PANW from {AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ}
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 2C (allowed symbol audit, IS window): PANW is the worst-performing scan symbol
on every metric — PF 0.51, TotalR −3.40R, MaxDD 3.40R, WR 22.2%, 9 signals.
It is the only scan symbol with PF below 1.0 in the IS window. Phase 5 (rejection share):
PANW's signals pass all quality filters but produce losing outcomes, meaning the filters
are not discriminating for PANW specifically.

The production exclusion list (ORB_EXCLUDED) already demonstrates the concept of
symbol-level exclusion based on structural characteristics. PANW's Phase 2C data
provides IS-window evidence to apply the same logic.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | Removing −3.40R drag from 9 losing signals improves aggregate PF |
| TotalR  | ↑         | +3.40R recovery from removing PANW's IS losses |
| MaxDD   | ↓         | Removing a −3.40R TotalR contributor reduces equity curve drawdown |
| Trades  | ↓         | −9 signals (8.3% volume reduction) |

**Risk:**
9 IS signals is a small per-symbol sample. PANW may have been adversely affected by
idiosyncratic IS-period events (earnings, sector rotation) not representative of
typical behaviour. Removing it could cause the system to miss a future recovery period.
PANW's ORB filter pass rate may also improve in a higher-ADX OOS regime.

---

### H-06 — Remove AAPL from ORB_EXCLUDED (add to scan list)

```
ID:         H-06
Parameter:  ORB_EXCLUDED symbol list
Change:     Remove AAPL from {AAPL, AMD, AVGO, COST, GOOGL, SPY, TSLA, UBER}
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 3 (excluded symbol audit, IS window): AAPL is the only excluded symbol to meet
the RECONSIDER threshold — PF 3.15, TotalR +4.30R, MaxDD 1.00R, WR 66.7%, 6 signals.
AAPL produced the highest PF of all 8 excluded symbols and the lowest MaxDD. Its original
exclusion rationale ("high correlation with QQQ; adds index noise") is structural but
was not reflected in IS outcome data — AAPL's signals were high-quality and
directionally non-redundant with QQQ's IS signals.

Phase 2C: QQQ itself had PF 1.44 and +2.20R TotalR in the IS window. AAPL's Phase 3
signals (PF 3.15) outperformed QQQ's signals substantially, arguing against the
"redundant noise" characterisation.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | AAPL Phase 3 PF (3.15) is substantially above system baseline (1.73) |
| TotalR  | ↑         | +4.30R IS contribution observed in Phase 3 |
| MaxDD   | ↓         | AAPL MaxDD 1.00R in Phase 3 — lowest of any symbol audited |
| Trades  | ↑         | +6 IS signals |

**Risk:**
6 signals is the smallest sample of any hypothesis in this list. The PF estimate of
3.15 has high variance at n=6. AAPL's correlation with QQQ may amplify same-direction
signal crowding on peak-momentum days, increasing system-level MaxDD in OOS. The
RECONSIDER threshold was designed as a floor for consideration, not a confirmation.

---

### H-07 (NEGATIVE) — No change to BREAK_DIST_MIN (F3 = 0.05)

```
ID:         H-07
Parameter:  ORB_BREAK_DIST_MIN  (F3)
Change:     NONE — no change warranted
Type:       Negative hypothesis / no-change finding
Approval:   NO-CHANGE CONFIRMED — F3 stays at 0.05 in all Phase 7 and Phase 8
            test configurations. No backtest required for this hypothesis.
```

**Evidence:**
Phase 5 (rejection share): F3 blocks exactly **1 bar** across all 8,208 breakout-window
bars in the IS window (0.0% share). It is the least active filter in the entire engine.

Phase 4 (break_dist_min sweep): PF range across the full sweep from 0.01 to 0.15 is
0.14 (STABLE — the smallest PF range of any parameter). Changing from 0.05 to 0.01
alters signal count by +1 and TotalR by −1.0R. Changing to 0.15 loses 6 signals
with no PF improvement. No value in the sweep produces a meaningful improvement.

Phase 5 (PF slope): shape is FALLING (tighter=worse), with slope at 0.0 when
loosening (no effect) and −1.333 per unit when tightening (marginal degradation only).

**Verdict:**
The data does not support modifying F3 in either direction. The filter imposes
no meaningful constraint in this IS window — it is active in name only.
This finding is documented here as a confirmed no-change decision.
F3 remains at 0.05 in all Phase 7 and Phase 8 test configurations.

**Risk:** N/A — no change is being proposed.

---

### H-08 — Remove MSFT from scan symbols

```
ID:         H-08
Parameter:  ORB scan symbol list
Change:     Remove MSFT from {AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ}
Approval:   APPROVED for Phase 7
```

**Evidence:**
Phase 2C (DST-safe IS window, full EDT + EST period): MSFT performance collapsed
when EST-period signals were included — PF 0.98, TotalR −0.20R, MaxDD 5.20R,
17 signals, WR 35.3%. Phase 2 (EDT-only, old clock): MSFT showed PF 3.24, +6.72R,
MaxDD 1.00R on 9 signals. The 8 additional signals from EST months (November 2025 –
March 2026) are the primary driver of degradation.

Phase 5 (ADX-RVOL interaction): MSFT had the highest ADX rejection count of any symbol
(545 bars in Phase 4 baseline run), suggesting many of MSFT's IS-window bars are
below the trend-strength threshold. The signals that DO fire in the EST period appear
to be low-quality breakouts that pass filters but lack follow-through.

The F4 rule (MSFT SHORT blocked when bias = NEUTRAL) already acknowledged MSFT-specific
structural issues in the original engine design. The Phase 2C data extends that concern
to the entire EST season.

**Prediction:**

| Metric  | Direction | Rationale |
|---------|-----------|-----------|
| PF      | ↑         | Removing 17 near-breakeven signals (PF 0.98) from the pool improves aggregate PF |
| TotalR  | ↑         | Recovering from −0.20R MSFT drag; aggregate TotalR increases |
| MaxDD   | ↓         | MSFT MaxDD 5.20R was highest among all allowed symbols |
| Trades  | ↓         | −17 signals (~15% volume reduction from the IS baseline) |

**Risk:**
MSFT was profitable in the EDT period (PF 3.24 on 9 signals). Removing it permanently
discards that performance alongside the EST-period losses. A more targeted hypothesis
(restrict MSFT to EDT months only) would preserve the good signals but requires a
DST-aware production change that is currently out of scope. Full removal is the
in-scope option that the data supports for the IS window.

---

## Hypothesis Summary Table

| ID | Parameter | Change | PF Pred | TotalR Pred | MaxDD Pred | Trades Pred | Type | Phase 6B Status |
|----|-----------|--------|---------|-------------|------------|-------------|------|-----------------|
| H-01 | RVOL_MIN | 1.5 → 1.4 | ↑ | ↑ | ↓ | ↑ | action | **APPROVED** |
| H-02 | ORB_RANGE_ATR_MIN | 2.0 → 1.0 | ↑ | ↑ | ↓ | ↑ | action | **APPROVED** |
| H-03 | ADX_MIN | 30 → 35 | ↑ | ↓ | ↓ | ↓ | action | **CONDITIONAL** (risk-control only) |
| H-04 | SESS_BRK_END | 11:30 → 12:00 ET | → | ↑ | → | ↑ | action | **APPROVED** |
| H-05 | Scan symbols | Remove PANW | ↑ | ↑ | ↓ | ↓ | action | **APPROVED** |
| H-06 | ORB_EXCLUDED | Remove AAPL | ↑ | ↑ | ↓ | ↑ | action | **APPROVED** |
| H-07 | BREAK_DIST_MIN (F3) | NONE | — | — | — | — | negative | **NO-CHANGE CONFIRMED** |
| H-08 | Scan symbols | Remove MSFT | ↑ | ↑ | ↓ | ↓ | action | **APPROVED** |

**Count:** 8 hypotheses (7 action, 1 negative). Maximum allowed: 10.  
**Phase 7 scope:** H-01, H-02, H-04, H-05, H-06, H-08 (performance) · H-03 (risk-control variant only)

---

## Constraints Confirmed

- [x] Every hypothesis cites a specific Phase 2–5 finding
- [x] Each hypothesis changes at most 2 parameters (all change exactly 1)
- [x] No hypothesis touches any out-of-scope component (stop/TP/execution/position sizing)
- [x] H-07 is a confirmed no-change finding, documented as required
- [x] Hypotheses were not written before Phases 2–5 were complete
- [x] No hypothesis has been tested yet

---

## Approval Gate — Phase 6B Decision Record

**Recorded:** 2026-06-26

| ID | Decision | Notes |
|----|----------|-------|
| H-01 | APPROVED | — |
| H-02 | APPROVED | — |
| H-03 | CONDITIONAL | Test as risk-control variant only. Not a performance-improvement candidate. PF gain acknowledged; TotalR reduction and signal loss are known accepted costs. |
| H-04 | APPROVED | — |
| H-05 | APPROVED | — |
| H-06 | APPROVED | — |
| H-07 | NO-CHANGE CONFIRMED | F3 remains at 0.05 in all configurations. No Phase 7 test required. |
| H-08 | APPROVED | — |

**Phase 7 may now begin for:** H-01, H-02, H-04, H-05, H-06, H-08  
**Phase 7 conditional scope:** H-03 (risk-control check only — must not be promoted as a primary improvement)  
**Excluded from Phase 7:** H-07 (no backtest needed; finding is final)
