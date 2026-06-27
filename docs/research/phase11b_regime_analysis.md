# Phase 11B — Regime Analysis

> **Scope:** Analysis and reporting only. No filters. No strategy changes. No optimization. No recommendations.

**Source:** `docs\research\phase11_regime_database.csv` (194 trading days)  
**Windows:** IS (2025-09-17 → 2026-04-30) + OOS (2026-05-01 → 2026-06-25)  

---

### By Market Regime (BULL / BEAR / NEUTRAL)

*SPY and QQQ both: EMA9 vs EMA20 on daily bars.*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| BULL | 129 | 64 | 50% | 104 | +39.39R | +0.379R | 50.0% | 1.87 | 34.3 | 0.984 | 51% |
| BEAR | 48 | 11 | 23% | 20 | -2.21R | -0.111R | 30.0% | 0.73 | 23.9 | 1.141 | 48% |
| NEUTRAL | 17 | 7 | 41% | 10 | +8.52R | +0.852R | 70.0% | 4.74 | 20.1 | 1.144 | 57% |

#### By Regime, split by IS / OOS

| Regime | Window | Days | SigDays | n | TotalR | AvgR/T | WR | PF |
|--------|--------|-----:|--------:|--:|-------:|-------:|---:|---:|
| BULL | IS | 93 | 43 | 72 | +29.39R | +0.408R | 51.4% | 2.01 |
| BULL | OOS | 36 | 21 | 32 | +10.00R | +0.312R | 46.9% | 1.62 |
| BEAR | IS | 48 | 11 | 20 | -2.21R | -0.111R | 30.0% | 0.73 |
| NEUTRAL | IS | 15 | 6 | 8 | +8.80R | +1.100R | 75.0% | 5.40 |
| NEUTRAL | OOS | 2 | 1 | 2 | -0.28R | -0.139R | 50.0% | 0.00 |

### By SPY Daily ADX Band

*ADX computed on daily OHLC bars with 14-period Wilder smoothing.*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| ADX <20 (weak trend) | 70 | 31 | 44% | 52 | +16.59R | +0.319R | 46.2% | 1.76 | 14.5 | 0.989 | 51% |
| ADX 20-35 (moderate) | 80 | 42 | 52% | 66 | +21.24R | +0.322R | 48.5% | 1.70 | 26.2 | 1.106 | 47% |
| ADX ≥35 (strong trend) | 44 | 9 | 20% | 16 | +7.88R | +0.492R | 56.2% | 3.24 | 63.5 | 0.987 | 59% |

### By SPY Daily Range Ratio

*range_ratio = today_range / 20-day avg range. >1 = expansion, <1 = compression.*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| RR <0.80 (compression) | 67 | 26 | 39% | 38 | +8.71R | +0.229R | 44.7% | 1.49 | 24.2 | 0.597 | 60% |
| RR 0.80-1.20 (normal) | 78 | 29 | 37% | 43 | +31.52R | +0.733R | 62.8% | 3.80 | 39.7 | 1.011 | 52% |
| RR ≥1.20 (expansion) | 49 | 27 | 55% | 53 | +5.47R | +0.103R | 39.6% | 1.20 | 24.2 | 1.679 | 37% |

### By Intraday Breadth at 10:00 ET

*% of BL_SYMS with close ≥ EMA20 at first bar of scan window (sm=30).*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| Breadth <33% (weak) | 69 | 31 | 45% | 60 | +16.24R | +0.271R | 45.0% | 1.64 | 28.5 | 1.241 | 21% |
| Breadth 33-67% (mixed) | 46 | 23 | 50% | 34 | +8.40R | +0.247R | 47.1% | 1.57 | 27.9 | 0.954 | 50% |
| Breadth ≥67% (strong) | 78 | 28 | 36% | 40 | +21.06R | +0.526R | 55.0% | 2.35 | 34.1 | 0.905 | 79% |

### By Average ORB Range (ATR units)

*Mean (ORB_high − ORB_low) / ATR across all scan symbols with a valid ORB.*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| ORB <2.5 ATR (tight) | 24 | 4 | 17% | 8 | +0.40R | +0.050R | 37.5% | 1.08 | 43.4 | 1.039 | 68% |
| ORB 2.5-3.5 ATR (avg) | 137 | 54 | 39% | 79 | +14.98R | +0.190R | 43.0% | 1.41 | 30.1 | 1.046 | 49% |
| ORB ≥3.5 ATR (wide) | 32 | 24 | 75% | 47 | +30.32R | +0.645R | 59.6% | 3.12 | 23.4 | 0.995 | 48% |

### By SPY Overnight Gap Size

*gap_pct = (open − prev_close) / prev_close × 100.*

| Group | Days | SigDays | Sig% | n | TotalR | AvgR/T | WR | PF | AvgADX | AvgRR | Breadth |
|-------|-----:|--------:|-----:|--:|-------:|-------:|---:|---:|-------:|------:|--------:|
| Gap ≤-0.50% (gap down) | 21 | 4 | 19% | 6 | -0.40R | -0.067R | 33.3% | 0.87 | 26.2 | 1.247 | 25% |
| Gap -0.50-0.00% (sm dn) | 60 | 28 | 47% | 39 | +19.46R | +0.499R | 53.8% | 2.22 | 29.7 | 0.996 | 42% |
| Gap 0.00-0.50% (sm up) | 79 | 38 | 48% | 68 | +12.32R | +0.181R | 42.6% | 1.39 | 32.6 | 0.995 | 60% |
| Gap ≥0.50% (gap up) | 34 | 12 | 35% | 21 | +14.32R | +0.682R | 61.9% | 3.71 | 29.5 | 1.076 | 64% |

### Regime × ADX Cross-Tab (AvgR/trade | n signals)

| Regime | ADX <20 | ADX 20-35 | ADX ≥35 |
|--------|---------|-----------|--------|
| BULL | +0.237R (n=32) | +0.426R (n=58) | +0.506R (n=14) |
| BEAR | +0.016R (n=12) | -0.533R (n=6) | +0.400R (n=2) |
| NEUTRAL | +1.100R (n=8) | -0.139R (n=2) | +0.000R (n=0) |

### May vs June 2026 — Feature Comparison

| Feature | May 2026 | Jun 2026 | Delta |
|---------|--------:|---------:|------:|
| SPY ADX (daily) | 25.775 | 25.975 | +0.200 |
| SPY range ratio | 0.972 | 1.343 | +0.371 |
| SPY ATR % of price | 1.025 | 1.168 | +0.142 |
| SPY gap % | 0.089 | 0.138 | +0.049 |
| Breadth at 10:00 ET | 54.450 | 37.017 | -17.433 |
| ORB range (ATR) | 3.140 | 3.146 | +0.006 |

| Outcome | May | Jun | Delta |
|---------|----:|----:|------:|
| Signal days | 13 | 9 | -4 |
| Total R | -1.200 | 10.923 | +12.123 |
| AvgR/trade | -0.067 | 0.683 | +0.749 |
| Win rate % | 33.333 | 62.500 | +29.167 |
| PF | 0.891 | 3.070 | +2.179 |

### Signal Days vs No-Signal Days — Feature Averages

| Feature | Signal days | No-signal days | Delta |
|---------|------------:|---------------:|------:|
| SPY ADX (daily) | 25.942 | 33.752 | -7.810 |
| SPY range ratio | 1.118 | 0.977 | +0.141 |
| SPY ATR % of price | 1.108 | 1.148 | -0.040 |
| SPY gap % | 0.092 | 0.037 | +0.054 |
| Breadth at 10:00 ET | 49.457 | 52.053 | -2.596 |
| ORB range (ATR) | 3.195 | 2.933 | +0.262 |

---

## Phase 11B Summary

**Best group:** REGIME:NEUTRAL  
AvgR/trade = +0.852R  WR = 70.0%  PF = 4.74  n = 10 signals

**Worst group:** REGIME:BEAR  
AvgR/trade = -0.111R  WR = 30.0%  PF = 0.73  n = 20 signals

**AvgR/trade spread (best − worst):** +0.963R

**Regime conditioning appears promising:** YES — AvgR/trade spread of +0.963R across dimension groups indicates material separation.

> No filters recommended. No parameters changed. Next step requires explicit user approval.
