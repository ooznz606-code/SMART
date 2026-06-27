# Phase 11C — Regime Filter Backtest

> **Scope:** Backtest only. No recommendations. No parameter changes. No strategy modifications.

**Baseline IS:** 100 trades · PF 1.73 · TotalR +35.98R · MaxDD 11.60R  
**Baseline OOS:** 34 trades · PF 1.54 · TotalR +9.72R · MaxDD 8.00R  

## Filter Definitions

| Filter | Condition |
|--------|----------|
| BASELINE | No filter (all days) |
| F-REG | Exclude BEAR regime days (SPY+QQQ EMA9 < EMA20) |
| F-RR | Range ratio 0.80-1.20 (normal daily range) |
| F-ORB | ORB avg ≥ 3.5 ATR (wide opening range) |
| F-ADX | SPY daily ADX ≥ 35 (strong trend) |
| F-RR+REG | F-RR AND F-REG |
| F-ORB+REG | F-ORB AND F-REG |
| F-RR+ORB | F-RR AND F-ORB |
| F-ALL | F-REG AND F-RR AND F-ORB |

> All thresholds taken directly from Phase 11B findings. Not tuned here.

---

## IS Performance by Filter  (2025-09-17 → 2026-04-30)

| Filter | Days | D% | n | T% | TotalR | ΔBase | AvgR/T | WR | PF | MaxDD |
|--------|-----:|---:|--:|---:|-------:|------:|-------:|---:|---:|------:|
| BASELINE | 156 | 100% | 100 | 100% | +35.98R | +0.00R | +0.360R | 49.0% | 1.73 | 11.60R |
| F-REG | 108 | 69% | 80 | 80% | +38.19R | +2.21R | +0.477R | 53.8% | 2.04 | 7.60R |
| F-RR | 64 | 41% | 29 | 29% | +24.20R | -11.78R | +0.834R | 65.5% | 3.42 | 3.20R |
| F-ORB | 26 | 17% | 38 | 38% | +20.80R | -15.18R | +0.547R | 55.3% | 2.22 | 5.00R |
| F-ADX | 44 | 28% | 16 | 16% | +7.88R | -28.10R | +0.492R | 56.2% | 2.13 | 3.52R |
| F-RR+REG | 48 | 31% | 25 | 25% | +22.60R | -13.38R | +0.904R | 68.0% | 3.82 | 3.20R |
| F-ORB+REG | 23 | 15% | 35 | 35% | +18.20R | -17.78R | +0.520R | 54.3% | 2.14 | 5.00R |
| F-RR+ORB | 12 | 8% | 13 | 13% | +15.00R | -20.98R | +1.154R | 76.9% | 6.00 | 1.00R |
| F-ALL | 10 | 6% | 12 | 12% | +13.20R | -22.78R | +1.100R | 75.0% | 5.40 | 1.00R |

## OOS Performance by Filter (2026-05-01 → 2026-06-25)

| Filter | Days | D% | n | T% | TotalR | ΔBase | AvgR/T | WR | PF | MaxDD |
|--------|-----:|---:|--:|---:|-------:|------:|-------:|---:|---:|------:|
| BASELINE | 38 | 100% | 34 | 100% | +9.72R | +0.00R | +0.286R | 47.1% | 1.54 | 8.00R |
| F-REG | 38 | 100% | 34 | 100% | +9.72R | +0.00R | +0.286R | 47.1% | 1.54 | 8.00R |
| F-RR | 14 | 37% | 14 | 41% | +7.32R | -2.40R | +0.523R | 57.1% | 2.22 | 3.00R |
| F-ORB | 6 | 16% | 9 | 26% | +9.52R | -0.20R | +1.058R | 77.8% | 5.76 | 1.00R |
| F-ADX | 0 | 0% | 0 | 0% | +0.00R | -9.72R | +0.000R | 0.0% | 0.00 | 0.00R |
| F-RR+REG | 14 | 37% | 14 | 41% | +7.32R | -2.40R | +0.523R | 57.1% | 2.22 | 3.00R |
| F-ORB+REG | 6 | 16% | 9 | 26% | +9.52R | -0.20R | +1.058R | 77.8% | 5.76 | 1.00R |
| F-RR+ORB | 4 | 11% | 6 | 18% | +6.92R | -2.80R | +1.154R | 83.3% | 7.92 | 1.00R |
| F-ALL | 4 | 11% | 6 | 18% | +6.92R | -2.80R | +1.154R | 83.3% | 7.92 | 1.00R |

## IS → OOS Consistency

| Filter | IS AvgR/T | OOS AvgR/T | IS PF | OOS PF | IS→OOS |
|--------|----------:|-----------:|------:|-------:|:------:|
| BASELINE | +0.360R | +0.286R | 1.73 | 1.54 | — |
| F-REG | +0.477R | +0.286R | 2.04 | 1.54 | HOLDS ✓ |
| F-RR | +0.834R | +0.523R | 3.42 | 2.22 | HOLDS ✓ |
| F-ORB | +0.547R | +1.058R | 2.22 | 5.76 | HOLDS ✓ |
| F-ADX | +0.492R | +0.000R | 2.13 | 0.00 | REVERSES ✗ |
| F-RR+REG | +0.904R | +0.523R | 3.82 | 2.22 | HOLDS ✓ |
| F-ORB+REG | +0.520R | +1.058R | 2.14 | 5.76 | HOLDS ✓ |
| F-RR+ORB | +1.154R | +1.154R | 6.00 | 7.92 | HOLDS ✓ |
| F-ALL | +1.100R | +1.154R | 5.40 | 7.92 | HOLDS ✓ |

## OOS Monthly Detail

| Filter | May n | May R | Jun n | Jun R | OOS Total |
|--------|------:|------:|------:|------:|----------:|
| BASELINE | 18 | -1.20R | 16 | +10.92R | +9.72R |
| F-REG | 18 | -1.20R | 16 | +10.92R | +9.72R |
| F-RR | 6 | +2.40R | 8 | +4.92R | +7.32R |
| F-ORB | 4 | +4.40R | 5 | +5.12R | +9.52R |
| F-ADX | 0 | +0.00R | 0 | +0.00R | +0.00R |
| F-RR+REG | 6 | +2.40R | 8 | +4.92R | +7.32R |
| F-ORB+REG | 4 | +4.40R | 5 | +5.12R | +9.52R |
| F-RR+ORB | 1 | +1.80R | 5 | +5.12R | +6.92R |
| F-ALL | 1 | +1.80R | 5 | +5.12R | +6.92R |

---

## Phase 11C Summary

> No recommendation written. No parameters changed. Next step requires explicit user approval.

**Filters ranked by OOS AvgR/trade:**

| Rank | Filter | IS AvgR/T | OOS AvgR/T | OOS PF | IS→OOS |
|-----:|--------|----------:|-----------:|-------:|:------:|
| 1 | F-RR+ORB | +1.154R | +1.154R | 7.92 | HOLDS ✓ |
| 2 | F-ALL | +1.100R | +1.154R | 7.92 | HOLDS ✓ |
| 3 | F-ORB | +0.547R | +1.058R | 5.76 | HOLDS ✓ |
| 4 | F-ORB+REG | +0.520R | +1.058R | 5.76 | HOLDS ✓ |
| 5 | F-RR | +0.834R | +0.523R | 2.22 | HOLDS ✓ |
| 6 | F-RR+REG | +0.904R | +0.523R | 2.22 | HOLDS ✓ |
| 7 | F-REG | +0.477R | +0.286R | 1.54 | HOLDS ✓ |
| 8 | F-ADX | +0.492R | +0.000R | 0.00 | REVERSES ✗ |

**Baseline:** IS +0.360R/trade → OOS +0.286R/trade
