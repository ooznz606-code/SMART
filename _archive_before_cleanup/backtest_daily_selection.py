# -*- coding: utf-8 -*-
"""
backtest_daily_selection.py  -  Daily Selection Diagnostic Backtest
====================================================================
Tests whether ranking candidates daily by research-proven features
and taking only the top-N per day improves WR, PF, and TotalR.

Scoring formula is derived from the multivariable feature study:
  HTF strength    (0-30 pts)  proven #1 predictor  (PF 1.42 alone)
  Birth freshness (0-30 pts)  proven #2 predictor  (PF 1.41 alone)
  Session quality (0-20 pts)  proven #3 predictor  (PF 1.38 alone)
  Move extension  (0-9  pts)  inverse of price_ext from birth
  Confluence      (0-10 pts)  how many of B1/B2/B3 fired
  Birth quality   (0-5  pts)  minor bonus

Zone freshness is NOT scored (feature study: |r| = 0.039, noise).
EMA50 distance is NOT scored (feature study: anti-predictive alone).

Four simulation variants:
  A)  All candidates (baseline pool)
  B)  Top-3 per day by rank score
  C)  Top-2 per day by rank score
  D)  Top-1 per day by rank score

Key output: score decile table proves (or disproves) that higher
rank score predicts better trade outcomes in this formula.

Birth mandatory: at least one of B1/B2/B3 must fire.
Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import csv
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from,
        _detect_zone, _zone_bounds, _zone_mid, _htf_direction,
        MIN_HISTORY, TP1_R,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}"); sys.exit(1)

try:
    from analyzer_x2 import Candle
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}"); sys.exit(1)

try:
    from backtest_entry_timing import (
        _get_profile, _swing_extreme, _swing_end,
        _bucket, _safe_avg,
        SYMBOLS, DAYS, SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
    )
except Exception as e:
    print(f"Cannot import backtest_entry_timing: {e}"); sys.exit(1)

try:
    from backtest_move_birth import _birth_expand, _birth_volume, _birth_structure, _cap
except Exception as e:
    print(f"Cannot import backtest_move_birth: {e}"); sys.exit(1)

try:
    from backtest_x3_birth_ict import _disp_quality
except Exception as e:
    print(f"Cannot import backtest_x3_birth_ict: {e}"); sys.exit(1)


# ── Pre-screen (loose -- purpose is pool volume) ──────────────────────────────
PRE_BIRTH_AGE_MAX  = 20
PRE_RECENCY_MAX    = 30
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_ZONE_FRESH_MIN = 0.40
PRE_ZONE_DIST_MAX  = 4.0
POOL_COOLDOWN      = 16    # bars between candidates, per symbol per direction
STOP_BUFFER        = 0.22


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Session score ─────────────────────────────────────────────────────────────

def _session_pts(ts: datetime) -> float:
    """
    Score session quality based on time.
    session_min = (h-9)*60 + m - 30; timestamps are UTC for US data.
    UTC 13:30 = ET 09:30 -> session_min = 240.

    ET 09:30-10:00  (session_min 240-270)  = NY open    = 20 pts
    ET 10:00-11:00  (session_min 270-330)  = morning    = 15 pts
    ET 11:00-14:15  (session_min 330-525)  = mid-day    =  8 pts
    ET 14:15-close  (session_min  >= 525)  = last hour  =  0 pts  (24% WR in study)
    """
    h, m = ts.hour, ts.minute
    sm   = (h - 9) * 60 + m - 30   # raw value (may be negative pre-market)
    if   sm <  270: return 20.0
    elif sm <  330: return 15.0
    elif sm <  525: return  8.0
    else:           return  0.0


# ── Research-grounded rank score ──────────────────────────────────────────────

def _rank_score(
    htf_strength:  float,
    birth_age:     int,
    ts:            datetime,
    price_ext_atr: float,
    confluence:    int,
    b_qual:        float,
) -> Tuple[float, Dict[str, float]]:
    """
    Composite rank score (0 - 104) grounded in feature study results.

    Component          Pts   Basis
    ----------------------------------------------------------------
    HTF alignment      0-30  proven: PF 1.42 alone, monotonic buckets
    Birth freshness    0-30  proven: PF 1.41 alone, clear age=1 premium
    Session quality    0-20  proven: PF 1.38 alone, last hour = 24% WR
    Move extension     0- 9  research: small extension = earlier entry
    Birth confluence   0-10  B1+B2+B3 confirmation
    Birth quality      0- 5  minor b_qual bonus
    ----------------------------------------------------------------
    Zone freshness: excluded -- feature study |r| = 0.039 (noise)
    EMA50 distance: excluded -- anti-predictive as standalone filter
    """
    # HTF: direction-adjusted EMA gap, already in "favorable = positive" form
    htf_pts  = max(0.0, min(float(htf_strength), 3.0)) * 10.0

    # Birth: each bar of age past 0 loses 7.5 pts; 0 at age >= 4
    birth_pts = max(0.0, 4.0 - float(birth_age)) * 7.5

    # Session
    sess_pts = _session_pts(ts)

    # Move extension: penalise large extension from birth (0 pts if > 3 ATR)
    ext_pts = max(0.0, 3.0 - float(price_ext_atr)) * 3.0

    # Confluence: 0/5/10 for 1/2/3 birth types
    conf_pts = max(0.0, float(confluence) - 1.0) * 5.0

    # Birth quality minor bonus
    bq_pts = float(b_qual) * 5.0

    total = htf_pts + birth_pts + sess_pts + ext_pts + conf_pts + bq_pts
    comps = {
        "htf": htf_pts, "birth": birth_pts, "sess": sess_pts,
        "ext": ext_pts, "conf": conf_pts, "bq": bq_pts,
    }
    return round(total, 2), comps


# ── Data download ─────────────────────────────────────────────────────────────

def _download(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)
    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  download error: {e}"); return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── Phase 1: Candidate pool generator ────────────────────────────────────────

def _build_pool(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    Generate every pre-screen candidate for one symbol.
    Birth is mandatory (at least one of B1/B2/B3 within PRE_BIRTH_AGE_MAX).
    POOL_COOLDOWN bars between candidates per direction.
    Rank score uses research-proven formula (HTF + birth + session).
    Outcome is simulated immediately.
    """
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    pool: List[Dict[str, Any]] = []
    last_bar: Dict[str, int] = {"LONG": -999, "SHORT": -999}

    for i in range(MIN_HISTORY, len(c15_all) - SIM_BARS - 1):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15[:40]

        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        htf_feed = c1h[-300:] if len(c1h) >= 60 else c15[-220:]
        market   = _market_state_from(zone_setup, c15_all[i].close)
        atr      = market.atr_14
        adx      = market.adx

        if adx < min_adx or adx > max_adx:
            continue

        htf  = _htf_direction(htf_feed, market)
        dirs = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            if (i - last_bar[direction]) < POOL_COOLDOWN:
                continue

            price     = c15_all[i].close
            sw_start, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_idx = len(c15) - 1 - bars_since

            # ── MANDATORY BIRTH ───────────────────────────────────────────
            b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr), bars_since)
            b_vol    = _cap(_birth_volume(c15, swing_idx),                  bars_since)
            b_struct = _cap(_birth_structure(c15, swing_idx, direction),    bars_since)
            b_any    = min(b_expand, b_vol, b_struct)

            if b_any > PRE_BIRTH_AGE_MAX:
                continue

            bars_after_birth = bars_since - b_any
            if bars_after_birth > PRE_RECENCY_MAX:
                continue

            # ── Birth quality ─────────────────────────────────────────────
            b_bar = swing_idx + b_any
            if not (0 <= b_bar < len(c15)):
                continue
            bc     = c15[b_bar]
            b_body = abs(bc.close - bc.open)
            b_qual = min(1.0, b_body / max(atr, 1e-9))
            if b_qual < PRE_B_QUAL_MIN:
                continue

            # ── Displacement quality ──────────────────────────────────────
            try:
                disp_qual = _disp_quality(c15, b_bar, direction, atr)
            except Exception:
                disp_qual = 0.0
            if disp_qual < PRE_DISP_QUAL_MIN:
                continue

            # ── Confluence ────────────────────────────────────────────────
            near = b_any + 3
            confluence = sum([
                1 if b_expand <= near else 0,
                1 if b_vol    <= near else 0,
                1 if b_struct <= near else 0,
            ])

            # ── Zone detection ────────────────────────────────────────────
            swing_in_setup = max(0, len(zone_setup) - 1 - bars_since)
            zone = _detect_zone(zone_setup, direction, atr, swing_in_setup)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb
            z_mid = _zone_mid(zone)
            fr = float(getattr(zone, "freshness", 0.5) or 0.5)
            zq = float(getattr(zone, "quality",   0.5) or 0.5)

            if fr < PRE_ZONE_FRESH_MIN:
                continue
            if abs(price - z_mid) / max(atr, 0.01) > PRE_ZONE_DIST_MAX:
                continue

            # ── HTF strength (direction-adjusted EMA gap on 1H) ──────────
            h_prices = [c.close for c in c1h] if len(c1h) >= 50 else [c.close for c in c15[-100:]]
            h_ema20  = _ema(h_prices, 20)
            h_ema50  = _ema(h_prices, 50)
            raw_htf  = (h_ema20 - h_ema50) / max(abs(h_ema50), 1e-9) * 100.0
            htf_strength = raw_htf if direction == "LONG" else -raw_htf

            # ── Move extension from birth close ───────────────────────────
            birth_close = bc.close
            if direction == "LONG":
                price_ext = max(0.0, (price - birth_close) / max(atr, 1e-9))
            else:
                price_ext = max(0.0, (birth_close - price) / max(atr, 1e-9))

            # ── Rank score (research-proven formula) ──────────────────────
            rank, comps = _rank_score(htf_strength, b_any, ts,
                                      price_ext, confluence, b_qual)

            # ── Entry / stop / TP ─────────────────────────────────────────
            entry = price
            stop  = ((z_bot - atr * STOP_BUFFER) if direction == "LONG"
                     else (z_top + atr * STOP_BUFFER))
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = ((entry + risk * TP1_R) if direction == "LONG"
                   else (entry - risk * TP1_R))

            # ── pct_done: diagnostic only (future bars) ───────────────────
            future  = c15_all[i + 1: i + 1 + SWING_LOOKFWD]
            sw_end  = _swing_end(future, direction)
            if direction == "LONG":
                total_mv = (sw_end - sw_start) if sw_end else None
                at_entry = entry - sw_start
            else:
                total_mv = (sw_start - sw_end) if sw_end else None
                at_entry = sw_start - entry
            pct = (round(at_entry / total_mv * 100, 1)
                   if (total_mv and total_mv > 0) else 50.0)

            # ── Outcome simulation ────────────────────────────────────────
            outcome = "BE"; r_mult = 0.0; ep = entry
            mfe = 0.0; mae = 0.0
            for fc in c15_all[i + 1: i + 1 + SIM_BARS + 1]:
                if direction == "LONG":
                    mfe = max(mfe, (fc.high - entry) / risk)
                    mae = min(mae, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome="LOSS"; ep=stop; r_mult=-1.0; break
                    if fc.high >= tp1:  outcome="WIN";  ep=tp1;  r_mult=TP1_R; break
                else:
                    mfe = max(mfe, (entry - fc.low)  / risk)
                    mae = min(mae, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome="LOSS"; ep=stop; r_mult=-1.0; break
                    if fc.low  <= tp1:  outcome="WIN";  ep=tp1;  r_mult=TP1_R; break
            else:
                ep = c15_all[min(i + SIM_BARS, len(c15_all) - 1)].close

            pool.append({
                "symbol":           symbol,
                "date":             ts.strftime("%Y-%m-%d"),
                "time":             ts.strftime("%H:%M"),
                "direction":        "CALL" if direction == "LONG" else "PUT",
                "entry":            round(entry, 4),
                "rank_score":       rank,
                # score components
                "sc_htf":           round(comps["htf"],   2),
                "sc_birth":         round(comps["birth"], 2),
                "sc_sess":          round(comps["sess"],  2),
                "sc_ext":           round(comps["ext"],   2),
                "sc_conf":          round(comps["conf"],  2),
                "sc_bq":            round(comps["bq"],    2),
                # features
                "htf_strength":     round(htf_strength,  3),
                "birth_age":        b_any,
                "bars_after_birth": bars_after_birth,
                "bars_since":       bars_since,
                "price_ext_atr":    round(price_ext,     3),
                "confluence":       confluence,
                "b_qual":           round(b_qual,        3),
                "disp_qual":        round(disp_qual,     3),
                "zone_fresh":       round(fr,            3),
                "zone_qual":        round(zq,            3),
                # diagnostic
                "pct_done":         pct,
                "bucket":           _bucket(pct),
                # outcome
                "result":           outcome,
                "r":                round(r_mult, 2),
                "mfe_r":            round(mfe,    2),
                "mae_r":            round(mae,    2),
            })
            last_bar[direction] = i

    return pool


# ── Daily selection ───────────────────────────────────────────────────────────

def _daily_select(pool: List[Dict], top_n: Optional[int]) -> List[Dict]:
    """Return all pool entries (top_n=None) or top-N per day by rank_score."""
    if top_n is None:
        return list(pool)
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for c in pool:
        by_date[c["date"]].append(c)
    selected: List[Dict] = []
    for date in sorted(by_date):
        ranked = sorted(by_date[date], key=lambda c: -c["rank_score"])
        selected.extend(ranked[:top_n])
    return selected


# ── Metrics ───────────────────────────────────────────────────────────────────

def _metrics(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "mfe": 0.0, "mae": 0.0, "late": 0.0, "vlate": 0.0,
                "avg_pct": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(t["r"] for t in trades if t["r"] > 0)
    gl   = abs(sum(t["r"] for t in trades if t["r"] < 0))
    dec  = wins + loss
    return {
        "n":       n,
        "wr":      wins / dec * 100 if dec else 0.0,
        "pf":      gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr":  sum(t["r"] for t in trades),
        "mfe":     _safe_avg([t["mfe_r"] for t in trades]),
        "mae":     _safe_avg([t["mae_r"] for t in trades]),
        "late":    sum(1 for t in trades if t.get("bucket") == "Late") / n * 100,
        "vlate":   sum(1 for t in trades if t.get("bucket") == "VeryLate") / n * 100,
        "avg_pct": _safe_avg([t.get("pct_done", 50.0) for t in trades]),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

N_DECILES = 10

def _print_decile_analysis(pool: List[Dict]) -> None:
    """
    Split pool into N_DECILES equal-size groups by rank_score.
    Proof test: does research-grounded rank_score predict outcome?
    """
    if not pool:
        return
    W = 84
    sp  = sorted(pool, key=lambda c: c["rank_score"])
    n   = len(sp)
    sz  = max(1, n // N_DECILES)

    print("\n" + "=" * W)
    print(f"  SCORE DECILE ANALYSIS  ({n} pool candidates)")
    print(f"  Score = HTF(30) + BirthFresh(30) + Session(20) + Extension(9) + Confluence(10) + BQ(5)")
    print(f"  Proof: if rank_score predicts outcome, WR/PF should rise with decile.")
    print("=" * W)
    hdr = "  {:>7}  {:>12}  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}  {:>8}"
    print(hdr.format("Decile", "ScoreRange", "N", "WR%", "PF", "TotalR", "AvgMFE", "AvgPct"))
    print("  " + "-" * (W - 2))

    for d in range(N_DECILES):
        lo  = d * sz
        hi  = (d + 1) * sz if d < N_DECILES - 1 else n
        grp = sp[lo:hi]
        if not grp:
            continue
        m   = _metrics(grp)
        s_lo = grp[0]["rank_score"]
        s_hi = grp[-1]["rank_score"]
        print(hdr.format(
            f"D{d+1:02d}",
            f"{s_lo:.1f}-{s_hi:.1f}",
            m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R", f"{m['avg_pct']:.1f}%",
        ))

    print("  " + "-" * (W - 2))
    ma = _metrics(pool)
    print(hdr.format(
        "ALL",
        f"{sp[0]['rank_score']:.1f}-{sp[-1]['rank_score']:.1f}",
        ma["n"], f"{ma['wr']:.1f}%", f"{ma['pf']:.2f}",
        f"{ma['totalr']:+.1f}R", f"{ma['mfe']:+.2f}R", f"{ma['avg_pct']:.1f}%",
    ))
    print("=" * W)

    # Top-3 vs bottom-3 decile comparison
    bot3 = sp[: sz * 3]
    top3 = sp[max(0, n - sz * 3):]
    mb   = _metrics(bot3); mt = _metrics(top3)
    lift = mt["wr"] - mb["wr"]
    pf_lift = mt["pf"] - mb["pf"]
    verdict = ("CONFIRMED" if lift >= 10 and pf_lift >= 0.20
               else ("PARTIAL" if lift >= 5 else "NOT CONFIRMED"))
    print(f"\n  Predictive lift  (top-3 deciles vs bottom-3 deciles):")
    print(f"    Bottom-3  N={mb['n']}  WR={mb['wr']:.1f}%  PF={mb['pf']:.2f}  TotalR={mb['totalr']:+.1f}R")
    print(f"    Top-3     N={mt['n']}  WR={mt['wr']:.1f}%  PF={mt['pf']:.2f}  TotalR={mt['totalr']:+.1f}R")
    print(f"    WR lift={lift:+.1f}pp  PF lift={pf_lift:+.2f}  -->  Score predicts outcome: {verdict}")


def _print_selection_table(
    pool:          List[Dict],
    trading_dates: Set[str],
) -> None:
    W = 88

    variants: List[Tuple[str, Optional[int]]] = [
        ("A) All candidates",  None),
        ("B) Top-3 per day",   3),
        ("C) Top-2 per day",   2),
        ("D) Top-1 per day",   1),
    ]

    print("\n" + "=" * W)
    print(f"  DAILY SELECTION RESULTS  ({len(trading_dates)} trading days)")
    print(f"  Selection pressure increases A -> D (taking fewer, higher-scored trades each day)")
    print("=" * W)
    hdr = "  {:<24}  {:>6}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}  {:>7}"
    print(hdr.format("Variant", "N", "WR%", "PF", "TotalR",
                     "AvgMFE", "AvgMAE", "LV%"))
    print("  " + "-" * (W - 2))

    for label, top_n in variants:
        sel = _daily_select(pool, top_n)
        m   = _metrics(sel)
        lv  = m["late"] + m["vlate"]
        # coverage
        sel_dates = len({t["date"] for t in sel})
        tpd = len(sel) / max(len(trading_dates), 1)
        print(hdr.format(
            label, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R",
            f"{m['mae']:+.2f}R", f"{lv:.1f}%",
        ))
        print(f"  {'':24}  coverage={sel_dates}/{len(trading_dates)} days"
              f"  ({sel_dates/max(len(trading_dates),1)*100:.0f}%)"
              f"  {tpd:.2f} trades/day  AvgPct={m['avg_pct']:.1f}%")

    print("=" * W)

    # Improvement summary
    all_m   = _metrics(_daily_select(pool, None))
    top1_m  = _metrics(_daily_select(pool, 1))
    print(f"\n  Quality lift  (Top-1 vs All):")
    print(f"    WR:    {all_m['wr']:.1f}% -> {top1_m['wr']:.1f}%  ({top1_m['wr']-all_m['wr']:+.1f}pp)")
    print(f"    PF:    {all_m['pf']:.2f} -> {top1_m['pf']:.2f}  ({top1_m['pf']-all_m['pf']:+.2f})")
    print(f"    LV%:   {all_m['late']+all_m['vlate']:.1f}% -> "
          f"{top1_m['late']+top1_m['vlate']:.1f}%  "
          f"({top1_m['late']+top1_m['vlate']-all_m['late']-all_m['vlate']:+.1f}pp)")
    print(f"    AvgMFE:{all_m['mfe']:+.2f}R -> {top1_m['mfe']:+.2f}R")


def _print_bucket_table(
    pool:          List[Dict],
    trading_dates: Set[str],
) -> None:
    """WR/N by bucket for each selection variant."""
    W = 82
    variants: List[Tuple[str, Optional[int]]] = [
        ("All",    None),
        ("Top-3",  3),
        ("Top-2",  2),
        ("Top-1",  1),
    ]
    print("\n" + "=" * W)
    print("  BUCKET BREAKDOWN  (pct_done diagnostic -- not used in scoring)")
    print("=" * W)
    hdr = "  {:<16}  {:>14}  {:>14}  {:>14}  {:>14}"
    print(hdr.format("Bucket", "All", "Top-3", "Top-2", "Top-1"))
    print("  " + "-" * (W - 2))

    sels = {v[0]: _daily_select(pool, v[1]) for v in variants}

    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        row = [bk]
        for label, _ in variants:
            grp = [t for t in sels[label] if t.get("bucket") == bk]
            m   = _metrics(grp)
            row.append(f"{len(grp)} / {m['wr']:.0f}%")
        print(hdr.format(*row))

    print("  " + "-" * (W - 2))
    for label, _ in variants:
        grp = sels[label]
        lv  = [t for t in grp if t.get("bucket") in ("Late", "VeryLate")]
        print(f"  {label:<16}  LV={len(lv)/max(len(grp),1)*100:.0f}%  "
              f"(Late+VeryLate / total)")
    print("=" * W)


def _print_per_symbol(
    pool:          List[Dict],
    trading_dates: Set[str],
) -> None:
    """Per-symbol for each variant."""
    W = 80
    print("\n" + "=" * W)
    print("  PER-SYMBOL  --  All / Top-1 / Top-2 / Top-3")
    print("=" * W)
    hdr = "{:<7}  {:>10}  {:>10}  {:>10}  {:>10}"
    print("  " + hdr.format("Symbol", "All", "Top-3", "Top-2", "Top-1"))
    print("  " + "-" * (W - 2))

    def fmt(trades: List[Dict]) -> str:
        m = _metrics(trades)
        if m["n"] == 0:
            return f"{'N/A':>10}"
        return f"N={m['n']} {m['wr']:.0f}% {m['pf']:.2f}"

    for sym in SYMBOLS:
        row = [sym]
        for top_n in [None, 3, 2, 1]:
            sel = _daily_select(pool, top_n)
            st  = [t for t in sel if t["symbol"] == sym]
            row.append(fmt(st))
        print("  " + hdr.format(*row))
    print("  " + "=" * (W - 2))
    # legend
    print("  Format: N=count  WR%  PF")


def _print_score_component_analysis(pool: List[Dict]) -> None:
    """Show which score component has the most influence on outcome."""
    W = 74
    print("\n" + "=" * W)
    print("  SCORE COMPONENT ANALYSIS  (correlation with Win/Loss)")
    print(f"  Pearson r for each component vs win_binary (1=WIN, 0=LOSS/BE)")
    print("=" * W)

    comps = ["sc_htf", "sc_birth", "sc_sess", "sc_ext", "sc_conf", "sc_bq", "rank_score"]
    labels = {
        "sc_htf":    "HTF alignment pts",
        "sc_birth":  "Birth freshness pts",
        "sc_sess":   "Session quality pts",
        "sc_ext":    "Move extension pts",
        "sc_conf":   "Confluence pts",
        "sc_bq":     "Birth quality pts",
        "rank_score":"Total rank score",
    }

    def pearson(x, y):
        n = len(x)
        if n < 3: return 0.0
        mx = sum(x)/n; my = sum(y)/n
        cov = sum((xi-mx)*(yi-my) for xi,yi in zip(x,y))
        vx  = sum((xi-mx)**2 for xi in x)
        vy  = sum((yi-my)**2 for yi in y)
        d   = (vx*vy)**0.5
        return round(cov/d, 4) if d > 0 else 0.0

    win_bin = [1.0 if t["result"]=="WIN" else 0.0 for t in pool]
    r_vals  = [float(t["r"]) for t in pool]
    sig     = 2.0 / len(pool)**0.5

    hdr = "  {:<24}  {:>8}  {:>8}  {:>8}"
    print(hdr.format("Component", "r(Win)", "r(R)", "Sig?"))
    print("  " + "-" * (W - 2))
    for c in comps:
        x = [float(t.get(c, 0)) for t in pool]
        rw = pearson(x, win_bin)
        rr = pearson(x, r_vals)
        is_sig = "*" if (abs(rw) >= sig or abs(rr) >= sig) else " "
        print(hdr.format(labels.get(c, c), f"{rw:+.3f}", f"{rr:+.3f}", is_sig))
    print("  " + "-" * (W - 2))
    print(f"  * = |r| >= {sig:.3f}  (approx significant, n={len(pool)})")
    print("=" * W)


def _write_csv(pool: List[Dict]) -> None:
    if not pool:
        return
    fields = [
        "symbol", "date", "time", "direction",
        "rank_score", "sc_htf", "sc_birth", "sc_sess", "sc_ext", "sc_conf", "sc_bq",
        "htf_strength", "birth_age", "bars_after_birth", "bars_since",
        "price_ext_atr", "confluence", "b_qual", "disp_qual",
        "zone_fresh", "zone_qual",
        "pct_done", "bucket", "result", "r", "mfe_r", "mae_r",
    ]
    path = "backtest_daily_selection.csv"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in pool:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  Pool CSV -> {path}  ({len(pool)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 86)
    print("  DAILY SELECTION DIAGNOSTIC BACKTEST")
    print(f"  Symbols      : {', '.join(SYMBOLS)}")
    print(f"  Window       : {DAYS} days")
    print(f"  Birth        : mandatory (>=1 of B1/B2/B3 within {PRE_BIRTH_AGE_MAX} bars)")
    print(f"  Pre-screen   : bq>={PRE_B_QUAL_MIN}  dq>={PRE_DISP_QUAL_MIN}"
          f"  zf>={PRE_ZONE_FRESH_MIN}  age<={PRE_BIRTH_AGE_MAX}")
    print(f"  Scoring      : HTF(30)+Birth(30)+Session(20)+Ext(9)+Conf(10)+BQ(5)")
    print(f"  Selection    : A=all  B=top3/day  C=top2/day  D=top1/day")
    print(f"  Run at       : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 86 + "\n")

    pool:          List[Dict] = []
    trading_dates: Set[str]  = set()

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        if not trading_dates:
            trading_dates = {c.timestamp.strftime("%Y-%m-%d")
                             for c in c15_all[MIN_HISTORY:]}

        cands = _build_pool(sym, c15_all, c1h_all)
        pool.extend(cands)
        wins = sum(1 for c in cands if c["result"] == "WIN")
        print(f"OK  {len(cands):>3} candidates  WR={wins/max(len(cands),1)*100:.0f}%")

    if not pool:
        print("\nNo candidates generated."); return

    by_date = {d for c in pool for d in [c["date"]]}
    print(f"\n  Total pool : {len(pool)} candidates  |  "
          f"{len(by_date)} days with candidates  |  "
          f"{len(trading_dates)} trading days")

    _print_decile_analysis(pool)
    _print_selection_table(pool, trading_dates)
    _print_bucket_table(pool, trading_dates)
    _print_score_component_analysis(pool)
    _print_per_symbol(pool, trading_dates)
    _write_csv(pool)

    # Final one-line summary
    ma  = _metrics(_daily_select(pool, None))
    m1  = _metrics(_daily_select(pool, 1))
    print(f"\n  {'-'*82}")
    print(f"  All candidates : N={ma['n']:>3}  WR={ma['wr']:.1f}%  PF={ma['pf']:.2f}"
          f"  TotalR={ma['totalr']:+.1f}R  LV={ma['late']+ma['vlate']:.1f}%")
    print(f"  Top-1 per day  : N={m1['n']:>3}  WR={m1['wr']:.1f}%  PF={m1['pf']:.2f}"
          f"  TotalR={m1['totalr']:+.1f}R  LV={m1['late']+m1['vlate']:.1f}%")
    print(f"  {'-'*82}")


if __name__ == "__main__":
    main()
