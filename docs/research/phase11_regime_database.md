# Phase 11 — Regime-Conditioned ORB Research: Database

> **Scope:** Data collection only. No hypotheses. No recommendations. No optimization.

**IS window:** 2025-09-17 → 2026-04-30  
**OOS window:** 2026-05-01 → 2026-06-25  
**Database:** `docs\research\phase11_regime_database.csv`  
**Rows:** 194 trading days  
**Columns:** 18  

---

## Column Definitions

| Column | Type | Source | Description |
|--------|------|--------|-------------|
| `date` | str | SPY 15m | Trading day YYYY-MM-DD |
| `ym` | str | derived | Year-month YYYY-MM |
| `window` | str | derived | IS or OOS |
| `spy_gap_pct` | float | SPY daily | (open − prev_close) / prev_close × 100 |
| `spy_atr14` | float | SPY daily | 14-day Wilder ATR on daily bars (points) |
| `spy_atr14_pct` | float | SPY daily | spy_atr14 / close × 100 (% of price, normalized) |
| `spy_range_ratio` | float | SPY daily | today range / 20-day avg range (1.0 = average) |
| `spy_adx14` | float | SPY daily | 14-day ADX on daily bars (trend strength) |
| `spy_ema9_gt_ema20` | int | SPY daily | 1 if daily EMA9 > EMA20, else 0 |
| `qqq_ema9_gt_ema20` | int | QQQ daily | 1 if daily EMA9 > EMA20, else 0 |
| `market_regime` | str | derived | BULL (both=1) / BEAR (both=0) / NEUTRAL (mixed) |
| `breadth_pct` | float | 15m sm=30 | % of BL_SYMS with close ≥ EMA20 at 10:00 ET |
| `orb_range_avg_atr` | float | 15m precomp | mean (ORB_high−ORB_low)/ATR across valid symbols |
| `orb_n_valid` | int | 15m precomp | count of symbols with a valid ORB on this day |
| `n_signals` | int | BL scan | baseline ORB signals fired (all BL_SYMS) |
| `total_r` | float | BL scan | sum of R multiples from baseline trades that day |
| `n_wins` | int | BL scan | count of winning baseline trades |
| `wr_pct` | float | BL scan | win rate % (null if n_signals=0) |

**ATR periods:** 14-day Wilder on daily bars for SPY/QQQ.  
**ADX period:** 14-day on daily bars (standard Wilder).  
**EMA periods:** fast=9, slow=20 on daily close.  
**Range ratio look-back:** 20 trading days.  
**Breadth bar:** first bar of scan window (sm=30, 10:00 ET open).  
**ORB ATR:** 14-period Wilder ATR from 15m bars at the sm=30 bar index.  
**Baseline:** BL_SYMS, BL params (locked from Phase 4).  

---

## Raw Database Statistics

> Statistics only. No analysis or recommendation.

### Row counts

| Window | Days |
|--------|-----:|
| IS  (2025-09-17 → 2026-04-30) | 156 |
| OOS (2026-05-01 → 2026-06-25) | 38 |
| **Total** | **194** |

### Market regime counts

| Regime | IS | OOS | Total |
|--------|---:|----:|------:|
| BULL | 93 | 36 | 129 |
| BEAR | 48 | 0 | 48 |
| NEUTRAL | 15 | 2 | 17 |

### Signal activity (baseline, combined window)

| Metric | Value |
|--------|------:|
| Days with ≥1 signal | 82 |
| Days with 0 signals | 112 |
| Total signals | 134 |
| Total R | +45.70R |

### Feature ranges (IS + OOS combined)

| Feature | Min | Max | Mean |
|---------|----:|----:|-----:|
| spy_gap_pct | -1.65% | 2.59% | 0.060% |
| spy_atr14 | 5.222 | 10.662 | 7.800 |
| spy_atr14_pct | 0.778% | 1.639% | 1.131% |
| spy_range_ratio | 0.344 | 3.815 | 1.037 |
| spy_adx14 | 0.0 | 100.0 | 30.45 |
| breadth_pct | 0% | 100% | 51.0% |
| orb_range_avg_atr | 2.015 | 4.418 | 3.044 |

### Per-month raw totals

| Month | Window | Bull | Neutral | Bear | Signal Days | Total R |
|-------|--------|-----:|--------:|-----:|------------:|--------:|
| 2025-09 | IS | 9 | 0 | 1 | 1 | +0.80R |
| 2025-10 | IS | 21 | 2 | 0 | 7 | +6.28R |
| 2025-11 | IS | 10 | 1 | 8 | 10 | +1.80R |
| 2025-12 | IS | 18 | 4 | 0 | 8 | -3.00R |
| 2026-01 | IS | 17 | 3 | 0 | 12 | +7.80R |
| 2026-02 | IS | 2 | 5 | 12 | 9 | +13.79R |
| 2026-03 | IS | 0 | 0 | 22 | 2 | -0.20R |
| 2026-04 | IS | 16 | 0 | 5 | 11 | +8.72R |
| 2026-05 | OOS | 20 | 0 | 0 | 13 | -1.20R |
| 2026-06 | OOS | 16 | 2 | 0 | 9 | +10.92R |

---

**Next step:** Phase 11 regime analysis requires explicit user approval before any filtering or hypothesis testing.
