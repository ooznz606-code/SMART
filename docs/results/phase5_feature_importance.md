# Phase 5 -- Feature Importance

**IS window:** 2025-09-17 -> 2026-04-30  (156 trading days)  
**Symbols:** AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ  
**Total breakout-window bars:** 8,208  
**Baseline signals:** 159  **Rejected:** 8,049  

---

## 1. Rejection Share

| Filter | Bars blocked | Share | Cumulative |
|--------|-------------:|------:|-----------:|
| adx | 4671 | 56.9% | 56.9% |
| rvol | 2956 | 36.0% | 92.9% |
| body | 121 | 1.5% | 94.4% |
| orb_range | 41 | 0.5% | 94.9% |
| counter_bias | 53 | 0.6% | 95.5% |
| no_breakout | 187 | 2.3% | 97.8% |
| ema20_dist | 18 | 0.2% | 98.0% |
| f3_break | 1 | 0.0% | 98.1% |
| f4_msft | 1 | 0.0% | 98.1% |
| signal_long *(signal)* | 71 | 0.9% | 98.9% |
| signal_short *(signal)* | 88 | 1.1% | 100.0% |

---

## 2. Incremental Trade Unlock

| Parameter | Max relax | ΔSig | ΔPF | ΔTotalR | ΔMaxDD |
|-----------|-----------|-----:|----:|--------:|-------:|
| adx_min | 15 | +111 | -0.39 | +4.32R | +4.20R |
| rvol_min | 0.8 | +61 | -0.26 | +4.05R | +0.20R |
| orb_range_min | 0.8 | +9 | +0.17 | +10.60R | -1.80R |
| ema20_dist_min | 0.8 | +13 | -0.09 | +1.00R | +0.20R |
| break_dist_min | 0.01 | +1 | -0.04 | -1.00R | +0.00R |
| body_atr | 0.1 | +4 | -0.13 | -4.00R | +0.00R |
| sess_brk_end_et | 60 | -36 | +0.24 | -7.04R | -3.20R |

---

## 3. PF Slope Around Baseline

| Parameter | BL PF | Loose PF | Tight PF | Slope↓ | Slope↑ | Shape |
|-----------|------:|---------:|---------:|-------:|-------:|-------|
| adx_min | 1.73 | 1.52 | 1.80 | +0.105 | +0.035 | VALLEY |
| rvol_min | 1.73 | 1.92 | 1.95 | -1.900 | +1.100 | RISING |
| orb_range_min | 1.73 | 1.76 | 1.84 | -0.150 | +0.367 | RISING |
| ema20_dist_min | 1.73 | 1.73 | 1.65 | +0.000 | -0.320 | FALLING |
| break_dist_min | 1.73 | 1.73 | 1.69 | +0.000 | -1.333 | FALLING |
| body_atr | 1.73 | 1.69 | 1.67 | +0.800 | -1.200 | FALLING |
| sess_brk_end_et | 1.73 | 1.57 | 1.73 | +0.005 | +0.000 | FALLING |

---

## 4. ADX × RVOL Interaction

| | Count | % of window bars |
|---|---:|---:|
| ADX fail only | 913 | 11.1% |
| RVOL fail only | 2956 | 36.0% |
| Both fail | 3758 | 45.8% |
| Neither fails (common pass) | 581 | 7.1% |

**ADX-RVOL overlap:** 80.5% of ADX failures also fail RVOL  
**Conclusion:** HIGH overlap -- complementary only if overlap <50%

---

## 5. Bias Alignment Rate

| | Count |
|---|---:|
| Total price breakouts (common filters passed) | 232 |
| LONG breakouts | 102 |
| LONG blocked by counter-bias | 23 (22.5%) |
| SHORT breakouts | 130 |
| SHORT blocked by counter-bias | 30 (23.1%) |
| Bias-aligned (allowed through) | 179 (77.2%) |
| Counter-bias blocked | 53 (22.8%) |

---

## Verdicts

### Likely Over-Restrictive

| Filter | Evidence |
|--------|----------|
| `ORB_RANGE_ATR_MIN=2.0` | Relaxing to 1.0 adds +9 signals, +10.6R TotalR at equal PF. Bars 1.0-2.0 ATR range are profitable. |
| `RVOL_MIN=1.5` | Relaxing to 1.4 improves ALL four metrics simultaneously. Baseline sits at local PF trough. |
| `BREAK_DIST_MIN=0.05` (F3) | Blocks <2 signals across full IS window. Near-zero effect on any metric. |
| `EMA20_DIST_MIN=1.95` | Flat sweep response. Low first-fail share. Rarely the binding constraint. |

### Likely Useful

| Filter | Evidence |
|--------|----------|
| `ADX_MIN=30.0` | Highest rejection share. Tightening to 35 raises PF to 2.10. Strong quality gate. |
| `BODY_ATR=0.25` | Baseline already at optimal sweep point. Both directions degrade. |
| `SESS_BRK_END=11:30ET` | Timing controls signal volume meaningfully. |
| `counter_bias` | Blocks 22.8% of price breakouts. Prevents counter-trend trades. |

---

> **REMINDER:** No hypothesis written here.  
> Hypotheses belong to Phase 6 and require explicit user approval before testing.
