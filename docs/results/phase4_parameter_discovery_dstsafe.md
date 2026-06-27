# Phase 4 -- Parameter Discovery (DST-Safe, IS Window)

**IS window:** 2025-09-17 -> 2026-04-30  (156 trading days)  
**Symbols:** AMZN, CRM, LLY, META, MSFT, NFLX, NVDA, PANW, QQQ  
**Clock:** `_sm_et` (zoneinfo America/New_York)  
**Note:** Discovery only. Each parameter swept in isolation.  

## Baseline

| ADX | RVOL | ORB_RANGE | EMA20_DIST | BREAK_DIST | BODY_ATR | SESS_END |
|-----|------|-----------|------------|------------|----------|----------|
| 30.0 | 1.5 | 2.0 | 1.95 | 0.05 | 0.25 | 11:30ET |

| Signals | WR% | PF | TotalR | MaxDD | AvgR | Trades/Day |
|--------:|----:|---:|-------:|------:|-----:|-----------:|
| 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 |

---

## Sweep Results

### ADX_MIN  (baseline = 30)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 15 | 211 | 43.1% | 1.34 | +40.30R | 12.00R | +0.191 | 1.353 | rvol |
| 20 | 186 | 43.0% | 1.35 | +36.23R | 10.26R | +0.195 | 1.192 | rvol |
| 25 | 138 | 45.7% | 1.48 | +35.56R | 8.40R | +0.258 | 0.885 | rvol |
| 28 | 111 | 45.9% | 1.52 | +30.58R | 7.00R | +0.275 | 0.712 | adx |
| 30 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 32 | 86 | 50.0% | 1.80 | +33.18R | 6.80R | +0.386 | 0.551 | adx |
| 35 | 67 | 53.7% | 2.10 | +32.58R | 6.40R | +0.486 | 0.429 | adx |
| 40 | 51 | 51.0% | 1.80 | +19.59R | 4.00R | +0.384 | 0.327 | adx |

- **Best PF:** `35` → PF=2.10, TotalR=+32.58R, sig=67
- **Best TotalR:** `15` → TotalR=+40.30R, PF=1.34, sig=211
- **Trades up / PF down:** `15` → sig=211, PF=1.34, MaxDD=12.00R
- **Trades up / PF down:** `20` → sig=186, PF=1.35, MaxDD=10.26R
- **Trades up / PF down:** `25` → sig=138, PF=1.48, MaxDD=8.40R
- **Trades up / PF down:** `28` → sig=111, PF=1.52, MaxDD=7.00R
- **Sensitivity:** MODERATE (PF range 0.76)

### RVOL_MIN  (baseline = 1.5)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 0.8 | 161 | 45.3% | 1.47 | +40.03R | 8.00R | +0.249 | 1.032 | adx |
| 1.0 | 148 | 47.3% | 1.58 | +44.51R | 6.20R | +0.301 | 0.949 | adx |
| 1.2 | 126 | 50.0% | 1.76 | +46.57R | 6.20R | +0.370 | 0.808 | adx |
| 1.4 | 109 | 51.4% | 1.92 | +47.16R | 5.20R | +0.433 | 0.699 | adx |
| 1.5 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 1.7 | 83 | 51.8% | 1.95 | +36.52R | 4.20R | +0.440 | 0.532 | adx |
| 2.0 | 61 | 52.5% | 1.98 | +27.32R | 3.72R | +0.448 | 0.391 | adx |

- **Best PF:** `2.0` → PF=1.98, TotalR=+27.32R, sig=61
- **Best TotalR:** `1.4` → TotalR=+47.16R, PF=1.92, sig=109
- **Trades up / PF down:** `0.8` → sig=161, PF=1.47, MaxDD=8.00R
- **Trades up / PF down:** `1.0` → sig=148, PF=1.58, MaxDD=6.20R
- **Sensitivity:** MODERATE (PF range 0.51)

### ORB_RANGE_MIN  (baseline = 2.0)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 0.8 | 109 | 51.4% | 1.90 | +46.58R | 6.00R | +0.427 | 0.699 | adx |
| 1.0 | 109 | 51.4% | 1.90 | +46.58R | 6.00R | +0.427 | 0.699 | adx |
| 1.2 | 108 | 50.9% | 1.87 | +44.78R | 7.80R | +0.415 | 0.692 | adx |
| 1.5 | 108 | 50.9% | 1.87 | +44.78R | 7.80R | +0.415 | 0.692 | adx |
| 1.8 | 103 | 49.5% | 1.76 | +38.58R | 7.80R | +0.375 | 0.660 | adx |
| 2.0 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 2.3 | 88 | 51.1% | 1.84 | +35.79R | 4.80R | +0.407 | 0.564 | adx |
| 2.6 | 74 | 50.0% | 1.81 | +29.65R | 5.00R | +0.401 | 0.474 | adx |

- **Best PF:** `0.8` → PF=1.90, TotalR=+46.58R, sig=109
- **Best TotalR:** `0.8` → TotalR=+46.58R, PF=1.90, sig=109
- **Sensitivity:** STABLE (PF range 0.18)

### EMA20_DIST_MIN  (baseline = 1.95)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 0.8 | 113 | 47.8% | 1.64 | +36.98R | 8.00R | +0.327 | 0.724 | adx |
| 1.0 | 111 | 47.7% | 1.64 | +36.18R | 8.00R | +0.326 | 0.712 | adx |
| 1.2 | 107 | 48.6% | 1.70 | +37.38R | 7.00R | +0.349 | 0.686 | adx |
| 1.5 | 105 | 49.5% | 1.76 | +39.38R | 6.20R | +0.375 | 0.673 | adx |
| 1.75 | 102 | 49.0% | 1.73 | +36.78R | 6.20R | +0.361 | 0.654 | adx |
| 1.95 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 2.2 | 96 | 47.9% | 1.65 | +31.58R | 7.80R | +0.329 | 0.615 | adx |
| 2.5 | 88 | 50.0% | 1.80 | +33.98R | 5.20R | +0.386 | 0.564 | adx |

- **Best PF:** `2.5` → PF=1.80, TotalR=+33.98R, sig=88
- **Best TotalR:** `1.5` → TotalR=+39.38R, PF=1.76, sig=105
- **Trades up / PF down:** `0.8` → sig=113, PF=1.64, MaxDD=8.00R
- **Trades up / PF down:** `1.0` → sig=111, PF=1.64, MaxDD=8.00R
- **Trades up / PF down:** `1.2` → sig=107, PF=1.70, MaxDD=7.00R
- **Sensitivity:** STABLE (PF range 0.16)

### BREAK_DIST_MIN  (baseline = 0.05)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 0.01 | 101 | 48.5% | 1.69 | +34.98R | 7.80R | +0.346 | 0.647 | adx |
| 0.03 | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 0.05 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 0.08 | 99 | 48.5% | 1.69 | +34.18R | 7.80R | +0.345 | 0.635 | adx |
| 0.1 | 98 | 46.9% | 1.58 | +29.58R | 7.80R | +0.302 | 0.628 | adx |
| 0.15 | 94 | 47.9% | 1.65 | +30.78R | 7.80R | +0.327 | 0.603 | adx |

- **Best PF:** `0.03` → PF=1.73, TotalR=+35.98R, sig=100
- **Best TotalR:** `0.03` → TotalR=+35.98R, PF=1.73, sig=100
- **Trades up / PF down:** `0.01` → sig=101, PF=1.69, MaxDD=7.80R
- **Sensitivity:** STABLE (PF range 0.14)

### BODY_ATR  (baseline = 0.25)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 0.1 | 104 | 47.1% | 1.60 | +31.98R | 7.80R | +0.307 | 0.667 | adx |
| 0.15 | 101 | 48.5% | 1.69 | +34.98R | 7.80R | +0.346 | 0.647 | adx |
| 0.2 | 101 | 48.5% | 1.69 | +34.98R | 7.80R | +0.346 | 0.647 | adx |
| 0.25 **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 0.3 | 100 | 48.0% | 1.67 | +34.12R | 7.80R | +0.341 | 0.641 | adx |
| 0.4 | 96 | 45.8% | 1.54 | +27.36R | 7.80R | +0.285 | 0.615 | adx |

- **Best PF:** `0.25` → PF=1.73, TotalR=+35.98R, sig=100
- **Best TotalR:** `0.25` → TotalR=+35.98R, PF=1.73, sig=100
- **Trades up / PF down:** `0.1` → sig=104, PF=1.60, MaxDD=7.80R
- **Trades up / PF down:** `0.15` → sig=101, PF=1.69, MaxDD=7.80R
- **Trades up / PF down:** `0.2` → sig=101, PF=1.69, MaxDD=7.80R
- **Sensitivity:** STABLE (PF range 0.18)

### SESS_BRK_END  (baseline = 11:30ET)

| Value | SIG | WR% | PF | TotalR | MaxDD | AvgR | T/Day | Top Rej |
|-------|----:|----:|---:|-------:|------:|-----:|------:|---------|
| 10:30ET | 64 | 53.1% | 1.97 | +28.94R | 4.60R | +0.452 | 0.410 | adx |
| 11:00ET | 90 | 46.7% | 1.57 | +26.72R | 7.80R | +0.297 | 0.577 | adx |
| 11:30ET **\*** | 100 | 49.0% | 1.73 | +35.98R | 7.80R | +0.360 | 0.641 | adx |
| 12:00ET | 110 | 49.1% | 1.73 | +39.98R | 7.60R | +0.363 | 0.705 | adx |
| 12:30ET | 116 | 49.1% | 1.72 | +41.26R | 7.60R | +0.356 | 0.744 | adx |

- **Best PF:** `10:30ET` → PF=1.97, TotalR=+28.94R, sig=64
- **Best TotalR:** `12:30ET` → TotalR=+41.26R, PF=1.72, sig=116
- **Trades up / PF down:** `12:30ET` → sig=116, PF=1.72, MaxDD=7.60R
- **Sensitivity:** STABLE (PF range 0.39)

---

## Summary Table

| Parameter | Baseline | Best PF value | Best PF | Best TotalR value | Best TotalR | Sensitivity |
|-----------|----------|--------------|---------|-------------------|-------------|-------------|
| adx_min | 30 | 35 | 2.10 | 15 | +40.30R | MODERATE |
| rvol_min | 1.5 | 2.0 | 1.98 | 1.4 | +47.16R | MODERATE |
| orb_range_min | 2.0 | 0.8 | 1.90 | 0.8 | +46.58R | STABLE |
| ema20_dist_min | 1.95 | 2.5 | 1.80 | 1.5 | +39.38R | STABLE |
| break_dist_min | 0.05 | 0.03 | 1.73 | 0.03 | +35.98R | STABLE |
| body_atr | 0.25 | 0.25 | 1.73 | 0.25 | +35.98R | STABLE |
| sess_brk_end_et | 11:30ET | 10:30ET | 1.97 | 12:30ET | +41.26R | STABLE |

---

## Notes

- All sweeps use IS window only (2025-09-17 to 2026-04-30).
- OOS window has not been inspected.
- No parameter value has been selected or recommended.
- Hypotheses will be generated in Phase 6 based on Phases 4 and 5 findings.
- Best PF / Best TotalR rows require >= 5 signals to be considered eligible.
