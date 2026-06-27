# ORB Research — Scope Lock

**Created:** 2026-06-26  
**Status:** Locked — no changes without explicit revision entry  
**Purpose:** Define exactly what this research programme will and will not touch,
before large datasets are collected and before any experiment is run.  
**Governed by:** `ORB_RESEARCH_PLAN.md` (workflow) · `DATA_AUDIT.md` (data requirements)  
**Production file:** `smart_analyzer_bridge_orb.py` (read-only during research)

---

## Guiding Principle

Every item that is IN SCOPE is a candidate for change.
Every item that is OUT OF SCOPE will not be analysed, tested, or modified —
regardless of what the research reveals about it.

If something is discovered during research that suggests an out-of-scope component
should change, that finding is documented and deferred to a separate research programme.
It does not expand the scope of this one.

---

## 1. IN SCOPE

The following components of the ORB engine may be researched and, if a candidate
passes all validation gates, modified.

### 1a. Symbol Lists

| Component | Current value | What research may determine |
|-----------|--------------|----------------------------|
| ORB scan symbols | LLY, PANW, CRM, QQQ, MSFT, META, AMZN, NVDA, NFLX | Whether any symbol degrades overall PF and should be excluded |
| ORB excluded symbols | AAPL, AMD, AVGO, COST, GOOGL, SPY, TSLA, UBER | Whether any excluded symbol would improve overall PF if added back |

### 1b. Filter Thresholds

These are the numeric gate values checked per bar in `scan_orb_live()`.
Each is a single float constant in `smart_analyzer_bridge_orb.py`.

| Constant | Current value | Description |
|----------|--------------|-------------|
| `ORB_ADX_MIN` | 30.0 | Minimum ADX(14) at signal bar |
| `ORB_RVOL_MIN` | 1.5 | Minimum relative volume vs 20-bar average |
| `ORB_RANGE_ATR_MIN` | 2.0 | Minimum ORB range (high−low) as ATR multiple |
| `ORB_EMA20_DIST_MIN` | 1.95 | Minimum price distance from EMA(20), in ATR units, direction-adjusted |
| `ORB_BREAK_DIST_MIN` | 0.05 | Minimum breakout distance beyond ORB level, in ATR units (F3) |
| `ORB_BODY_ATR` | 0.25 | Minimum candle body as fraction of ATR |

### 1c. Time Window

| Constant | Current value | ET equivalent | Description |
|----------|--------------|--------------|-------------|
| `SESS_BRK_END` | 390 (session minutes) | 11:30 ET | Breakout window close — last bar eligible for a signal |

> `SESS_ORB_DONE` (10:00 ET, the ORB range lock time) is the window open.
> It is the boundary between ORB accumulation and breakout scanning.
> Research may evaluate whether extending the window end time to 12:00 ET or later
> increases valid signal volume. The window open (10:00 ET) is fixed — see section 2.

### 1d. Daily Caps and Direction Rules

| Constant | Current value | Description |
|----------|--------------|-------------|
| `TOP_N_DAY` | 3 | Maximum live ORB signals emitted per trading day |
| `ORB_MAX_DIR_PER_DAY` | 2 | F2: maximum signals in the same direction per day |

### 1e. Special Symbol Rules

| Rule | Current behaviour | Research question |
|------|------------------|------------------|
| F4 | MSFT SHORT blocked when bias = NEUTRAL | Does this rule improve or degrade MSFT PF? Should it be removed, kept, or generalised to other symbols? |

---

## 2. OUT OF SCOPE

The following components will not be researched, tested, or modified in this programme.
They are listed explicitly to prevent scope creep.

### 2a. Entry Detection Logic

The mechanism that defines what constitutes a breakout is not under research.

| Component | Why out of scope |
|-----------|-----------------|
| Breakout condition: `b.close > oh and b.close > b.open` (LONG) | This IS the ORB definition. Changing it changes the strategy type, not the parameters. |
| Breakout condition: `b.close < ol and b.close < b.open` (SHORT) | Same — the ORB signal identity |
| ORB range accumulation (9:30–10:00 ET) | Fixed by NYSE session structure |
| ORB range lock time `SESS_ORB_DONE` (10:00 ET) | Fixed — changing it redefines what "Opening Range" means |
| Session open `SESS_OPEN` (9:30 ET) | NYSE fixed |
| Session cutoff `SESS_CUTOFF` (14:15 ET) | Hard safety rule — not a research variable |

### 2b. Indicator Calculation Methods

The formulas used to compute indicators are fixed. Only the thresholds applied to their
output values are in scope.

| Indicator | Formula | Status |
|-----------|---------|--------|
| ATR | Wilder smoothing, 14-period | Fixed |
| ADX | Wilder DX smoothing, 14-period | Fixed |
| RVOL | Current volume ÷ 20-bar average | Fixed |
| EMA(20) | Standard EMA, 2/(20+1) multiplier | Fixed |
| Bias (SPY+QQQ EMA9 vs EMA20) | Current implementation | Fixed |

### 2c. Risk and Trade Management

| Component | Current value | Status |
|-----------|--------------|--------|
| Stop loss | Entry ± 1.5 ATR | Fixed |
| Take profit (TP1) | Entry ± 2.7 ATR (1.8R) | Fixed |
| Maximum hold period | 40 bars | Fixed |
| Trailing stop logic | Handled by execution engine | Fixed |

### 2d. Execution

| Component | Status |
|-----------|--------|
| Contract selection | Fixed — `execution.py` not modified |
| Order routing | Fixed — `execution.py` not modified |
| Fill logic | Fixed |
| B+C Sniper priority (ORB yields if BC has active signal) | Fixed |
| Signal TTL (`SIGNAL_TTL_SEC = 300`) | Fixed — not a trading parameter |

### 2e. Position Sizing

Position sizing (number of contracts, dollar risk per trade) is determined by the
execution engine and account balance. It is not modified by this research.

### 2f. Operational Parameters

| Constant | Value | Status |
|----------|-------|--------|
| `SCAN_INTERVAL_SEC` | 60 s | Fixed |
| `DATA_STALE_MIN` | 30 min | Fixed |
| `MIN_LB` | 60 bars | Fixed — indicator warmup requirement, not a trading gate |

---

## 3. Research Priority Order

Priorities are ordered by how quickly the finding changes whether subsequent work
is worth doing. Symbol and filter questions must be answered before hypotheses
are written; window and cap questions are secondary.

| Priority | Topic | Phases | Rationale |
|----------|-------|--------|-----------|
| 1 | Allowed symbol audit | Phase 2 | Eliminates dead weight from scan list early |
| 2 | Excluded symbol audit | Phase 3 | May expand the candidate pool before any threshold is touched |
| 3 | ADX threshold sensitivity | Phase 4 | Highest rejection share in current scan; most impactful lever |
| 4 | RVOL threshold sensitivity | Phase 4 | Second-highest rejection share |
| 5 | ORB range threshold sensitivity | Phase 4 | Affects how many days have a valid ORB |
| 6 | EMA20 distance threshold sensitivity | Phase 4 | Momentum quality gate |
| 7 | Break distance threshold sensitivity (F3) | Phase 4 | Fine-grained fakeout filter |
| 8 | Candle body minimum sensitivity | Phase 4 | Lowest expected impact; tested last |
| 9 | Window end time sensitivity | Phase 4 | Timing question; answered after filter questions |
| 10 | Direction cap (F2) and daily cap (Top-N) | Phase 4 | Caps constrain volume, not signal quality |
| 11 | F4 rule evaluation | Phase 5 | Special case; analysed as part of feature importance |
| 12 | Hypothesis generation | Phase 6 | Requires Phases 2–5 output; cannot begin earlier |
| 13 | IS hypothesis testing | Phase 7 | Sequential — requires Phase 6 approval |
| 14 | IS combination testing | Phase 8 | Sequential — requires Phase 7 results |
| 15 | Walk-forward OOS validation | Phase 9 | Sequential — one candidate only, untouched OOS window |

---

## 4. Estimated Number of Experiments

### Phase 4 — Parameter Discovery (sweeps, each parameter in isolation)

| Parameter | Range | Step | Points |
|-----------|-------|------|--------|
| ADX minimum | 15 → 40 | 2.5 | 11 |
| RVOL minimum | 0.8 → 2.5 | 0.1 | 18 |
| ORB range / ATR | 0.5 → 3.5 | 0.25 | 13 |
| EMA20 distance / ATR | 0.5 → 3.5 | 0.25 | 13 |
| Break distance / ATR (F3) | 0.01 → 0.15 | 0.01 | 15 |
| Candle body / ATR | 0.10 → 0.50 | 0.05 | 9 |
| Window end (ET) | 10:30 → 13:00 | 15 min | 10 |
| Daily cap (Top-N) | 1 → 5 | 1 | 5 |
| Direction cap (F2) | 1 → 4 | 1 | 4 |
| **Phase 4 total** | | | **~98** |

### Phase 7 — Hypothesis Validation

Maximum 10 hypotheses → **10 experiments**

### Phase 8 — Combination Validation

Maximum 15 combinations → **15 experiments**

### Phase 9 — Walk-forward Validation

1 candidate × 3 rolling windows → **3 runs**

### Total

| Scenario | Experiments |
|----------|------------|
| Minimum (few hypotheses pass) | ~115 |
| Expected | ~126 |
| Maximum (full combination matrix) | ~150 |

---

## 5. Expected Deliverables

| Phase | Deliverable | Format |
|-------|-------------|--------|
| Phase 2 | `docs/results/phase2_allowed_symbol_audit.csv` | One row per scan symbol |
| Phase 3 | `docs/results/phase3_excluded_symbol_audit.csv` | One row per excluded symbol |
| Phase 4 | `docs/results/phase4_parameter_discovery.csv` | One row per (parameter, value) point |
| Phase 5 | `docs/results/phase5_feature_importance.md` | Ranked filter list with rejection share, sensitivity, interaction flag |
| Phase 6 | `docs/results/phase6_hypotheses.md` | Up to 10 hypotheses, user-approved before Phase 7 |
| Phase 7 | `docs/results/phase7_hypothesis_validation.csv` | One row per hypothesis with IS verdict |
| Phase 8 | `docs/results/phase8_combination_validation.csv` | One row per combination with IS verdict |
| Phase 9 | `docs/results/phase9_walkforward.csv` | One row per (candidate, window) with OOS verdict |
| Phase 10 | `docs/results/phase10_recommendation.md` | Written report with MERGE / DO NOT MERGE / CONDITIONAL verdict |

All files are written to `docs/results/`. Nothing is written to the project root or
to any directory used by the live bot.

---

## 6. Exit Criteria Per Phase

A phase is complete only when every criterion below is met.
A phase that is "mostly done" is not done.

### Phase 2 — Allowed Symbol Audit
- [ ] All 9 scan symbols have measured ADX, RVOL, ORB range, EMA20 distance, and break distance distributions (percentiles: 10th, 25th, 50th, 75th, 90th)
- [ ] Per-symbol signal count, win rate, PF, and MaxDD recorded under current parameters
- [ ] Breakout frequency and direction split recorded per symbol
- [ ] `phase2_allowed_symbol_audit.csv` written and reviewed
- [ ] No hypothesis has been written

### Phase 3 — Excluded Symbol Audit
- [ ] All 8 excluded symbols measured with the same metrics as Phase 2
- [ ] Hypothetical signal count and outcome recorded per excluded symbol
- [ ] Original exclusion rationale reviewed against measured data
- [ ] `phase3_excluded_symbol_audit.csv` written and reviewed
- [ ] No change made to the exclusion list
- [ ] No hypothesis has been written

### Phase 4 — Parameter Discovery
- [ ] All 9 in-scope parameters swept across their full defined range
- [ ] Each sweep point produces: trade count, win rate, PF, TotalR, MaxDD
- [ ] All sweeps run in isolation (one parameter at a time)
- [ ] `phase4_parameter_discovery.csv` written and reviewed
- [ ] No parameter value has been selected or recommended

### Phase 5 — Feature Importance
- [ ] Rejection share ranked for all in-scope filters
- [ ] Incremental trade-unlock rate calculated per parameter
- [ ] PF slope identified for each parameter around its current baseline value
- [ ] Inter-filter correlation documented for the two highest-rejection filters
- [ ] Bias alignment rate measured by symbol and condition
- [ ] `phase5_feature_importance.md` written and reviewed
- [ ] No hypothesis has been written

### Phase 6 — Hypothesis Generation
- [ ] Each hypothesis cites the specific Phase 2–5 finding that motivates it (required — no citation = rejected)
- [ ] Maximum 10 hypotheses
- [ ] Each hypothesis changes at most 2 in-scope parameters
- [ ] No hypothesis touches any out-of-scope component
- [ ] User has explicitly approved the hypothesis list
- [ ] `phase6_hypotheses.md` written, approved, and locked before Phase 7 begins

### Phase 7 — Hypothesis Validation (In-Sample)
- [ ] Every approved hypothesis from Phase 6 tested on the IS window
- [ ] Each hypothesis receives verdict: IS-PASS / IS-FAIL / IS-MIXED
- [ ] At least one IS-PASS exists; if not, return to Phase 6
- [ ] `phase7_hypothesis_validation.csv` written and reviewed

### Phase 8 — Combination Validation (In-Sample)
- [ ] Only IS-PASS hypotheses from Phase 7 are combined
- [ ] Maximum 15 combinations tested
- [ ] A single best candidate is identified that achieves IS-PASS on all 4 gate metrics
- [ ] If no combination achieves IS-PASS, return to Phase 6
- [ ] `phase8_combination_validation.csv` written and reviewed
- [ ] Single candidate documented before Phase 9 begins

### Phase 9 — Walk-forward Validation (Out-of-Sample)
- [ ] Exactly one candidate tested (selected in Phase 8)
- [ ] OOS window opened for the first time at the start of this phase
- [ ] 3 rolling windows tested; no re-tuning between windows
- [ ] Candidate passes all 4 gate metrics in at least 2 of 3 windows; if not, rejected and return to Phase 6
- [ ] OOS window not re-examined after a failure to re-tune
- [ ] `phase9_walkforward.csv` written and reviewed

### Phase 10 — Final Recommendation
- [ ] Report covers: candidate summary, IS results, OOS results, gate verdicts, risk section
- [ ] Verdict is one of: MERGE / DO NOT MERGE / CONDITIONAL
- [ ] If CONDITIONAL, the condition is stated explicitly and is verifiable
- [ ] `phase10_recommendation.md` written and delivered to user
- [ ] User has explicitly confirmed the recommendation before any production file is touched

---

## 7. Definition of "Production Ready"

A candidate configuration is production ready when **all six conditions are simultaneously true:**

| # | Condition |
|---|-----------|
| 1 | Candidate passes all 4 gate metrics (PF, TotalR, MaxDD, trade count) in the IS window |
| 2 | Candidate passes all 4 gate metrics in at least 2 of 3 OOS walk-forward windows |
| 3 | Phase 10 recommendation is **MERGE** (not CONDITIONAL, not DO NOT MERGE) |
| 4 | User has **explicitly confirmed** the Phase 10 recommendation in writing |
| 5 | The candidate modifies only in-scope parameters listed in section 1 of this document |
| 6 | No out-of-scope component (section 2) has been modified, directly or indirectly |

If any condition is not met, the candidate is not production ready.
A candidate that meets 5 of 6 conditions is not production ready.

> "Production ready" does not mean "merge immediately."
> It means the evidence is sufficient to support a merge decision.
> The merge decision itself belongs to the user.

---

## Revision History

| Date | Change |
|------|--------|
| 2026-06-26 | Initial scope lock created |
