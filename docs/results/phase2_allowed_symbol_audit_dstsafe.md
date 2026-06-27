# Phase 2C — DST-Safe Allowed Symbol Audit

**IS window:** 2025-09-17 → 2026-04-30  
**IS trading days:** 156  
**Clock:** `_sm_et` via `zoneinfo` `America/New_York`  
**Session constants:** SESS_OPEN_ET=0  SESS_ORB_DONE_ET=30  SESS_BRK_END_ET=120  
**Scan symbols:** AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ  

---

## Results

| SYM | Signals | W% | PF | TotalR | MaxDD | Top Rejection | Comment |
|-----|--------:|---:|---:|-------:|------:|---------------|----------|
| AMZN | 14 | 42.9% | 1.54 | +3.79R | 2.00R | adx (503) | good -- keep |
| CRM | 12 | 66.7% | 4.00 | +10.80R | 1.00R | adx (489) | good -- keep |
| LLY | 6 | 50.0% | 1.80 | +2.40R | 3.00R | adx (488) | marginal positive |
| META | 11 | 72.7% | 4.49 | +10.46R | 1.00R | adx (517) | good -- keep |
| MSFT | 17 | 35.3% | 0.98 | -0.20R | 5.20R | adx (545) | breakeven |
| NFLX | 8 | 62.5% | 2.44 | +4.33R | 2.00R | adx (538) | good -- keep |
| NVDA | 14 | 50.0% | 1.80 | +5.60R | 3.00R | adx (533) | good -- keep |
| PANW | 9 | 22.2% | 0.51 | -3.40R | 3.40R | adx (515) | losing -- review |
| QQQ | 9 | 44.4% | 1.44 | +2.20R | 5.00R | adx (543) | marginal positive |

---

## Comparison vs Old Phase 2 (UTC clock, full dataset)

| SYM | Signals | PF | TotalR | MaxDD | Top Rejection |
|-----|--------:|---:|-------:|------:|---------------|
| AMZN | 10→14 | 0.77→1.54 | -1.60R→+3.79R | 3.00R→2.00R | adx→adx |
| CRM | 8→12 | 3.00→4.00 | +6.00R→+10.80R | 2.00R→1.00R | adx→adx |
| LLY | 7→6 | 1.35→1.80 | +1.40R→+2.40R | 3.00R→3.00R | adx→adx |
| META | 8→11 | 11.66→4.49 | +10.66R→+10.46R | 1.00R→1.00R | adx→adx |
| MSFT | 9→17 | 3.24→0.98 | +6.72R→-0.20R | 1.00R→5.20R | adx→adx |
| NFLX | 6→8 | 2.77→2.44 | +3.53R→+4.33R | 1.00R→2.00R | adx→adx |
| NVDA | 12→14 | 1.29→1.80 | +2.00R→+5.60R | 3.00R→3.00R | adx→adx |
| PANW | 8→9 | 1.08→0.51 | +0.40R→-3.40R | 4.00R→3.40R | adx→adx |
| QQQ | 5→9 | 2.70→1.44 | +3.40R→+2.20R | 1.00R→5.00R | adx→adx |

---

## Notes

- Old Phase 2 used UTC raw clock: EST months (Nov 2025 - Mar 2026) produced zero ORB signals (44% of dataset lost)
- Phase 2C uses `_sm_et`: all IS trading days now form ORB correctly
- IS window (2025-09-17 to 2026-04-30) includes 5 EST months; DST fix unlocks this data
- Signal counts remain small; PF estimates are directional, not statistically final
- Phase 3 (excluded symbol audit) should use the same DST-safe clock and IS window
