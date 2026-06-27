# Phase 3 -- DST-Safe Excluded Symbol Audit

**IS window:** 2025-09-17 -> 2026-04-30  
**IS trading days:** 156  
**Clock:** `_sm_et` via `zoneinfo` `America/New_York`  
**Note:** Hypothetical scan only -- no production change made  

### Classification rules

| Class | Criteria |
|-------|----------|
| RECONSIDER | PF >= 2.0 AND TotalR > 0 AND MaxDD <= 3R AND signals >= 5 |
| REJECT     | PF < 1.2 OR TotalR <= 0 OR MaxDD > 4R |
| WATCHLIST  | otherwise |

---

## Results

| SYM | Signals | W% | PF | TotalR | MaxDD | Class | Top Rejection | Original Rationale |
|-----|--------:|---:|---:|-------:|------:|-------|---------------|--------------------|
| AAPL | 6 | 66.7% | 3.15 | +4.30R | 1.00R | RECONSIDER | adx (579) | high correlation with QQQ; adds index noise |
| AMD | 16 | 37.5% | 0.86 | -1.39R | 3.79R | REJECT | adx (476) | erratic ORB behaviour; high false-breakout rate |
| AVGO | 12 | 33.3% | 0.90 | -0.80R | 4.00R | REJECT | adx (569) | low liquidity relative to notional; gap risk |
| COST | 8 | 12.5% | 0.26 | -5.20R | 7.00R | REJECT | adx (555) | slow mover; rarely meets RVOL threshold |
| GOOGL | 11 | 36.4% | 0.94 | -0.44R | 3.20R | REJECT | adx (550) | high correlation with QQQ; redundant signal source |
| SPY | 2 | 50.0% | 1.80 | +0.80R | 1.00R | WATCHLIST | adx (591) | used for bias calculation; conflict of interest |
| TSLA | 15 | 46.7% | 1.58 | +4.60R | 2.20R | WATCHLIST | adx (559) | extreme volatility; ATR-based SL frequently breached pre-TP |
| UBER | 11 | 36.4% | 1.03 | +0.20R | 3.00R | REJECT | adx (583) | low ADX consistency; trend structure unreliable |

---

## Summary

**Best excluded symbol:** TSLA (PF 1.58, TotalR +4.60R, WATCHLIST)

**Worst excluded symbol:** COST (PF 0.26, TotalR -5.20R, REJECT)

**Phase 6 candidates:** AAPL

Symbols meeting RECONSIDER criteria should be considered for a Phase 6 hypothesis: remove from `ORB_EXCLUDED` and validate in Phase 7 IS window.

---

## Rejection key

`adx` = ADX < 30.0  |  `rvol` = RVOL < 1.5  |  `orb_range` = range < 2.0 ATR  
`ema20_dist` = dist < 1.95 ATR  |  `counter_bias` = against SPY+QQQ  
`f3_break` = break dist < 0.05 ATR  |  `no_breakout` = price did not cross ORB
