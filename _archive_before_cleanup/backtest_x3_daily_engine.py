# -*- coding: utf-8 -*-
"""
backtest_x3_daily_engine.py  -  X3 Daily Selection Engine (Diagnostic)
=======================================================================
Architecture (4 phases per day):
  Phase 1  Candidate pool generation   -- all symbols, loose pre-screen,
                                           birth mandatory (>=1 of B1/B2/B3)
  Phase 2  Rank score per candidate    -- composite ICT quality score (0-110)
  Phase 3  Daily selection             -- top-1 + top-2 by score (diff symbols)
  Phase 4  Simulation + reporting      -- outcome already in pool; just select

Key design constraint: birth event is MANDATORY.
  b_any = min(b_expand, b_vol, b_struct) must be <= PRE_BIRTH_AGE_MAX.
  If no birth fires within the window, the candidate is discarded.
  Birth quality, freshness, confluence then enter the RANK SCORE, not a hard gate.

Selection pressure (top-N per day from a large pool) is what maintains high WR
-- NOT tight absolute gates.  This is the architectural difference from X3.

First output block: WR / PF / N by score decile.
  This is the proof-of-concept test.  If higher deciles show materially
  higher WR and PF, the scoring formula is predictive and selection is valid.

Do not modify analyzer_x2.  Diagnostic only.
"""
from __future__ import annotations

import sys
import csv
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
    from analyzer_x2 import Candle, _volume_ratio
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


# ── Pre-screen thresholds (loose -- purpose is pool volume, not quality) ──────
PRE_BIRTH_AGE_MAX  = 20    # bars from swing extreme to birth  (X3 used 10)
PRE_RECENCY_MAX    = 30    # bars from birth to entry          (X3 used 5)
PRE_B_QUAL_MIN     = 0.60  # birth candle body / ATR           (X3 used 0.85)
PRE_DISP_QUAL_MIN  = 0.40  # displacement composite            (X3 used 0.55)
PRE_ZONE_FRESH_MIN = 0.50  # zone freshness                    (X3 used 0.80)
PRE_ZONE_DIST_MAX  = 3.5   # zone midpoint distance in ATR     (X3 used 3.2)

# ── Selection ─────────────────────────────────────────────────────────────────
MIN_SELECT_SCORE = 45.0   # floor: skip day if best candidate < this
POOL_COOLDOWN    = 16     # bars between candidates, per symbol per direction

# ── Simulation ────────────────────────────────────────────────────────────────
STOP_BUFFER = 0.22        # zone boundary buffer (ATR units)

# ── Decile analysis ───────────────────────────────────────────────────────────
N_DECILES = 10


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
        print(f"  download error: {e}")
        return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── Session bonus ─────────────────────────────────────────────────────────────

def _session_bonus(ts: datetime) -> float:
    """
    NY open (09:30-11:00 ET) = 5 pts -- best institutional activity.
    PM push (14:00-16:00 ET) = 3 pts -- volume picks up.
    Mid-day (11:00-14:00 ET) = 0 pts.
    Assumes timestamps are in Eastern Time (standard for US 15m yfinance data).
    """
    h, m = ts.hour, ts.minute
    if (h == 9 and m >= 30) or h == 10:
        return 5.0
    if h in (14, 15):
        return 3.0
    return 0.0


# ── Phase 2: Rank score ───────────────────────────────────────────────────────

def _rank_score(
    b_any:      int,
    b_qual:     float,
    disp_qual:  float,
    zone_fresh: float,
    confluence: int,
    htf:        str,
    ts:         datetime,
) -> Tuple[float, Dict[str, float]]:
    """
    Composite rank score.  Max = 110.

    Component            Formula                      Max
    ---------------------------------------------------------
    Birth freshness      max(0, 10 - b_any) * 3.0      30
    Birth quality        b_qual * 25.0                  25
    Displacement qual    disp_qual * 20.0               20
    Zone freshness       zone_fresh * 15.0              15
    Confluence (B1/B2/B3) confluence * 3.33             10
    HTF alignment        5 if aligned, 2 if neutral      5
    Session              NY open 5, PM 3, else 0         5
    ---------------------------------------------------------
    Total                                               110

    Higher score = better expected trade quality.
    Score is computed purely from real-time data at the candidate bar.
    No future prices involved.
    """
    sc_fresh = max(0.0, 10 - b_any)  * 3.0
    sc_bq    = b_qual                * 25.0
    sc_dq    = disp_qual             * 20.0
    sc_zf    = zone_fresh            * 15.0
    sc_conf  = confluence            * 3.33
    sc_htf   = 5.0 if htf != "NEUTRAL" else 2.0
    sc_ses   = _session_bonus(ts)
    total    = sc_fresh + sc_bq + sc_dq + sc_zf + sc_conf + sc_htf + sc_ses
    comps = {
        "fresh": sc_fresh, "bq": sc_bq, "dq": sc_dq,
        "zf": sc_zf, "conf": sc_conf, "htf": sc_htf, "ses": sc_ses,
    }
    return round(total, 2), comps


# ── Phase 1: Candidate pool generator ────────────────────────────────────────

def _build_pool(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:
    """
    Scan all bars for one symbol and generate every pre-screen candidate.

    Rules:
    - Birth is mandatory: b_any <= PRE_BIRTH_AGE_MAX (at least one of B1/B2/B3)
    - All other thresholds are loose (purpose: volume, not quality)
    - Quality enters the rank_score, not a hard gate
    - POOL_COOLDOWN bars between candidates per direction (prevents stacking)
    - Outcome is simulated immediately (future bars already available in backtest)
    - pct_done is computed from future bars for diagnostic reporting only
    """
    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    candidates: List[Dict[str, Any]] = []
    last_bar: Dict[str, int] = {"LONG": -999, "SHORT": -999}

    for i in range(MIN_HISTORY, len(c15_all) - SIM_BARS - 1):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        htf_feed = c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:]
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

            # ── MANDATORY BIRTH CHECK ─────────────────────────────────────
            b_expand = _cap(_birth_expand(c15, swing_idx, direction, atr), bars_since)
            b_vol    = _cap(_birth_volume(c15, swing_idx),                  bars_since)
            b_struct = _cap(_birth_structure(c15, swing_idx, direction),    bars_since)
            b_any    = min(b_expand, b_vol, b_struct)

            if b_any > PRE_BIRTH_AGE_MAX:
                continue  # no birth fired -- discard

            bars_after_birth = bars_since - b_any
            if bars_after_birth > PRE_RECENCY_MAX:
                continue

            # ── Birth quality (loose floor) ───────────────────────────────
            b_bar = swing_idx + b_any
            if not (0 <= b_bar < len(c15)):
                continue
            bc     = c15[b_bar]
            b_body = abs(bc.close - bc.open)
            b_qual = min(1.0, b_body / max(atr, 1e-9))

            if b_qual < PRE_B_QUAL_MIN:
                continue

            # ── Displacement quality (loose floor) ────────────────────────
            disp_qual = _disp_quality(c15, b_bar, direction, atr)
            if disp_qual < PRE_DISP_QUAL_MIN:
                continue

            # ── Confluence: how many birth types fired near b_any ─────────
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
            zq = float(getattr(zone, "quality",   0.7) or 0.7)
            fr = float(getattr(zone, "freshness", 0.7) or 0.7)

            if fr < PRE_ZONE_FRESH_MIN:
                continue
            if abs(price - z_mid) / max(atr, 0.01) > PRE_ZONE_DIST_MAX:
                continue

            # ── Rank score ────────────────────────────────────────────────
            rank, comps = _rank_score(b_any, b_qual, disp_qual, fr,
                                      confluence, htf, ts)

            # ── Entry / stop / TP ─────────────────────────────────────────
            entry = price
            stop  = ((z_bot - atr * STOP_BUFFER) if direction == "LONG"
                     else (z_top + atr * STOP_BUFFER))
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = ((entry + risk * TP1_R) if direction == "LONG"
                   else (entry - risk * TP1_R))

            # ── DIAGNOSTIC: pct_done (future bars -- NOT an entry gate) ───
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
            outcome = "BE"; r_mult = 0.0; ep = entry; mfe = 0.0; mae = 0.0
            for fc in c15_all[i + 1: i + 1 + SIM_BARS + 1]:
                if direction == "LONG":
                    mfe = max(mfe, (fc.high - entry) / risk)
                    mae = min(mae, (fc.low  - entry) / risk)
                    if fc.low  <= stop: outcome = "LOSS"; ep = stop; r_mult = -1.0; break
                    if fc.high >= tp1:  outcome = "WIN";  ep = tp1;  r_mult = TP1_R; break
                else:
                    mfe = max(mfe, (entry - fc.low)  / risk)
                    mae = min(mae, (entry - fc.high) / risk)
                    if fc.high >= stop: outcome = "LOSS"; ep = stop; r_mult = -1.0; break
                    if fc.low  <= tp1:  outcome = "WIN";  ep = tp1;  r_mult = TP1_R; break
            else:
                ep = c15_all[min(i + SIM_BARS, len(c15_all) - 1)].close

            candidates.append({
                "symbol":           symbol,
                "date":             ts.strftime("%Y-%m-%d"),
                "time":             ts.strftime("%H:%M"),
                "direction":        "CALL" if direction == "LONG" else "PUT",
                "entry":            round(entry,    4),
                "stop":             round(stop,     4),
                "tp1":              round(tp1,      4),
                "rank_score":       rank,
                # -- score components --
                "birth_age":        b_any,
                "bars_after_birth": bars_after_birth,
                "b_qual":           round(b_qual,    3),
                "disp_qual":        round(disp_qual, 3),
                "zone_fresh":       round(fr,        3),
                "zone_qual":        round(zq,        3),
                "confluence":       confluence,
                "htf":              htf,
                "sc_fresh":         round(comps["fresh"], 2),
                "sc_bq":            round(comps["bq"],    2),
                "sc_dq":            round(comps["dq"],    2),
                "sc_zf":            round(comps["zf"],    2),
                "sc_conf":          round(comps["conf"],  2),
                "sc_htf":           round(comps["htf"],   2),
                "sc_ses":           round(comps["ses"],   2),
                # -- diagnostic --
                "pct_done":         pct,
                "bucket":           _bucket(pct),
                # -- outcome --
                "result":           outcome,
                "r":                round(r_mult, 2),
                "mfe_r":            round(mfe,    2),
                "mae_r":            round(mae,    2),
                "exit_price":       round(ep,     4),
            })
            last_bar[direction] = i

    return candidates


# ── Phase 3: Daily selection ──────────────────────────────────────────────────

def _daily_select(pool: List[Dict]) -> Tuple[List[Dict], Dict[str, int]]:
    """
    For each trading day:
      1. Filter pool candidates with rank_score >= MIN_SELECT_SCORE
      2. Sort by rank_score descending
      3. Top 1: highest-scored candidate (any symbol)
      4. Top 2: highest-scored candidate from a DIFFERENT symbol

    Returns:
      selected   -- list of chosen trades with 'selection_rank' (1 or 2)
      day_counts -- dict: date -> number of qualifying candidates that day
    """
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for c in pool:
        by_date[c["date"]].append(c)

    selected: List[Dict] = []
    day_counts: Dict[str, int] = {}

    for date in sorted(by_date):
        qualifying = [c for c in by_date[date]
                      if c["rank_score"] >= MIN_SELECT_SCORE]
        qualifying.sort(key=lambda c: -c["rank_score"])
        day_counts[date] = len(qualifying)

        if not qualifying:
            continue

        top1 = qualifying[0].copy()
        top1["selection_rank"] = 1
        selected.append(top1)

        for c in qualifying[1:]:
            if c["symbol"] != top1["symbol"]:
                top2 = c.copy()
                top2["selection_rank"] = 2
                selected.append(top2)
                break

    return selected, day_counts


# ── Metrics helper ────────────────────────────────────────────────────────────

def _metrics(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "late": 0.0, "vlate": 0.0, "avg_pct": 0.0, "avg_r": 0.0}
    w   = sum(1 for t in trades if t["result"] == "WIN")
    l   = sum(1 for t in trades if t["result"] == "LOSS")
    gw  = sum(t["r"] for t in trades if t["r"] > 0)
    gl  = abs(sum(t["r"] for t in trades if t["r"] < 0))
    dec = w + l
    return {
        "n":       n,
        "wr":      w / dec * 100 if dec else 0.0,
        "pf":      gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr":  sum(t["r"] for t in trades),
        "late":    sum(1 for t in trades if t.get("bucket") == "Late")     / n * 100,
        "vlate":   sum(1 for t in trades if t.get("bucket") == "VeryLate") / n * 100,
        "avg_pct": _safe_avg([t.get("pct_done", 50.0) for t in trades]),
        "avg_r":   _safe_avg([t["r"] for t in trades]),
    }


# ── Reporting ─────────────────────────────────────────────────────────────────

def _print_decile_analysis(pool: List[Dict]) -> None:
    """
    Split pool into N_DECILES equal-size groups by rank_score.
    Report WR, PF, TotalR, N per decile.
    This is the proof-of-concept test: does rank_score predict outcome?
    """
    if not pool:
        return
    W = 82
    sorted_pool = sorted(pool, key=lambda c: c["rank_score"])
    n   = len(sorted_pool)
    sz  = max(1, n // N_DECILES)

    print("\n" + "=" * W)
    print(f"  SCORE DECILE ANALYSIS  (all {n} pool candidates, pre-selection)")
    print(f"  Proof test: does rank_score (0-110) predict trade outcome?")
    print(f"  Pool cooldown={POOL_COOLDOWN}b  birth mandatory  pre-screen loose")
    print("=" * W)
    hdr = "  {:>6}  {:>10}  {:>6}  {:>6}  {:>5}  {:>8}  {:>7}"
    print(hdr.format("Decile", "ScoreRange", "N", "WR%", "PF", "TotalR", "AvgPct"))
    print("  " + "-" * (W - 2))

    for d in range(N_DECILES):
        lo  = d * sz
        hi  = (d + 1) * sz if d < N_DECILES - 1 else n
        grp = sorted_pool[lo:hi]
        if not grp:
            continue
        m   = _metrics(grp)
        s_lo = grp[0]["rank_score"]
        s_hi = grp[-1]["rank_score"]
        label = f"D{d+1:02d}"
        print(hdr.format(
            label,
            f"{s_lo:.1f}-{s_hi:.1f}",
            m["n"],
            f"{m['wr']:.1f}%",
            f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R",
            f"{m['avg_pct']:.1f}%",
        ))

    print("  " + "-" * (W - 2))
    m_all = _metrics(pool)
    print(hdr.format(
        "ALL", f"{sorted_pool[0]['rank_score']:.1f}-{sorted_pool[-1]['rank_score']:.1f}",
        m_all["n"], f"{m_all['wr']:.1f}%", f"{m_all['pf']:.2f}",
        f"{m_all['totalr']:+.1f}R", f"{m_all['avg_pct']:.1f}%",
    ))
    print("=" * W)

    # Trend check: compare bottom 3 deciles vs top 3 deciles
    bot3 = sorted_pool[: sz * 3]
    top3 = sorted_pool[max(0, n - sz * 3):]
    mb   = _metrics(bot3)
    mt   = _metrics(top3)
    lift_wr = mt["wr"] - mb["wr"]
    print(f"\n  Predictive lift (top-3 deciles vs bottom-3 deciles):")
    print(f"    Bottom-3  N={mb['n']}  WR={mb['wr']:.1f}%  PF={mb['pf']:.2f}  TotalR={mb['totalr']:+.1f}R")
    print(f"    Top-3     N={mt['n']}  WR={mt['wr']:.1f}%  PF={mt['pf']:.2f}  TotalR={mt['totalr']:+.1f}R")
    verdict = "CONFIRMED" if lift_wr >= 10 else ("WEAK" if lift_wr >= 5 else "NOT CONFIRMED")
    print(f"    WR lift = {lift_wr:+.1f}pp  -->  Score predicts outcome: {verdict}")


def _print_pool_stats(pool: List[Dict], trading_dates: Set[str]) -> None:
    W = 76
    print("\n" + "=" * W)
    print(f"  POOL STATISTICS  ({len(pool)} total candidates, {len(trading_dates)} trading days)")
    print("=" * W)

    by_date: Dict[str, List] = defaultdict(list)
    for c in pool:
        by_date[c["date"]].append(c)

    counts_per_day = [len(v) for v in by_date.values()]
    days_no_pool   = len(trading_dates) - len(by_date)
    days_qualified = sum(1 for v in by_date.values()
                         if any(c["rank_score"] >= MIN_SELECT_SCORE for c in v))

    avg_per_day = _safe_avg(counts_per_day) if counts_per_day else 0.0
    max_per_day = max(counts_per_day) if counts_per_day else 0

    print(f"  Total candidates      : {len(pool)}")
    print(f"  Trading days          : {len(trading_dates)}")
    print(f"  Days with pool entry  : {len(by_date)}"
          f"  ({len(by_date)/max(len(trading_dates),1)*100:.0f}%)")
    print(f"  Days no pool entry    : {days_no_pool}")
    print(f"  Days with qualifying  : {days_qualified}"
          f"  (score >= {MIN_SELECT_SCORE})")
    print(f"  Avg candidates / day  : {avg_per_day:.1f}  (max={max_per_day})")

    # Distribution of candidates per day
    dist = defaultdict(int)
    for cnt in counts_per_day:
        bucket = 0 if cnt == 0 else (1 if cnt == 1 else (2 if cnt <= 3 else 3))
        dist[bucket] += 1
    for days_no_pool_iter in range(days_no_pool):
        dist[0] += 1

    labels = {0: "0 candidates", 1: "1 candidate", 2: "2-3 candidates", 3: "4+ candidates"}
    total_days = len(trading_dates)
    print(f"\n  Pool depth distribution:")
    for k in sorted(labels):
        cnt = dist[k]
        print(f"    {labels[k]:20} : {cnt:3}  ({cnt/max(total_days,1)*100:.0f}%)")

    # Per-symbol candidate count
    print(f"\n  Candidates per symbol:")
    hdr2 = "  {:<7}  {:>6}  {:>8}  {:>6}  {:>5}  {:>7}"
    print(hdr2.format("Symbol", "Count", "% Pool", "WR%", "PF", "TotalR"))
    print("  " + "-" * 50)
    total_pool = max(len(pool), 1)
    for sym in SYMBOLS:
        st = [c for c in pool if c["symbol"] == sym]
        m  = _metrics(st)
        print(hdr2.format(sym, m["n"], f"{m['n']/total_pool*100:.0f}%",
                          f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R"))
    print("  " + "=" * 50)


def _print_daily_coverage(
    selected:       List[Dict],
    day_counts:     Dict[str, int],
    trading_dates:  Set[str],
) -> None:
    W = 76
    print("\n" + "=" * W)
    print(f"  DAILY COVERAGE  (top-1 + top-2 selection, floor={MIN_SELECT_SCORE})")
    print("=" * W)

    # Days with trades
    sel_dates = defaultdict(list)
    for t in selected:
        sel_dates[t["date"]].append(t)

    days_with_1  = sum(1 for v in sel_dates.values() if len(v) >= 1)
    days_with_2  = sum(1 for v in sel_dates.values() if len(v) >= 2)
    days_no_qual = sum(1 for d in trading_dates if day_counts.get(d, 0) == 0)
    total_days   = len(trading_dates)

    print(f"  Trading days          : {total_days}")
    print(f"  Days with top-1 trade : {days_with_1}"
          f"  ({days_with_1/max(total_days,1)*100:.0f}%)")
    print(f"  Days with top-2 trade : {days_with_2}"
          f"  ({days_with_2/max(total_days,1)*100:.0f}%)")
    print(f"  Days no qualifying    : {days_no_qual}"
          f"  (score < {MIN_SELECT_SCORE} all day)")
    print(f"  Avg trades / day      : {len(selected)/max(total_days,1):.2f}")

    # Top-1 score distribution
    t1_scores = [t["rank_score"] for t in selected if t.get("selection_rank") == 1]
    if t1_scores:
        print(f"\n  Top-1 rank score stats:")
        print(f"    Min={min(t1_scores):.1f}  "
              f"Avg={sum(t1_scores)/len(t1_scores):.1f}  "
              f"Max={max(t1_scores):.1f}")

    # Daily WR (profitable day = net R > 0 across top-1 + top-2)
    daily_r = {}
    for t in selected:
        daily_r[t["date"]] = daily_r.get(t["date"], 0.0) + t["r"]
    profitable_days = sum(1 for v in daily_r.values() if v > 0)
    loss_days       = sum(1 for v in daily_r.values() if v < 0)
    be_days         = sum(1 for v in daily_r.values() if v == 0)

    print(f"\n  Daily P&L:")
    print(f"    Profitable days : {profitable_days}"
          f"  ({profitable_days/max(len(daily_r),1)*100:.0f}%)")
    print(f"    Loss days       : {loss_days}"
          f"  ({loss_days/max(len(daily_r),1)*100:.0f}%)")
    print(f"    Breakeven days  : {be_days}")
    if daily_r:
        avg_dr = sum(daily_r.values()) / len(daily_r)
        print(f"    Avg daily R     : {avg_dr:+.2f}R")
    print("=" * W)


def _print_selection_results(selected: List[Dict]) -> None:
    W = 82
    top1 = [t for t in selected if t.get("selection_rank") == 1]
    top2 = [t for t in selected if t.get("selection_rank") == 2]
    m_all = _metrics(selected)
    m1    = _metrics(top1)
    m2    = _metrics(top2)

    print("\n" + "=" * W)
    print("  SELECTION RESULTS  -- All / Top-1 / Top-2")
    print("=" * W)
    hdr = "{:<22}  {:>16}  {:>16}  {:>16}"
    print(hdr.format("Metric", "All Selected", "Top-1 Only", "Top-2 Only"))
    print("-" * W)

    def row(label, a, p, r, fmt=""):
        if   fmt == "pct":  fv = lambda v: f"{v:.1f}%"
        elif fmt == "r":    fv = lambda v: f"{v:+.2f}R"
        elif fmt == "f2":   fv = lambda v: f"{v:.2f}"
        else:               fv = str
        print(hdr.format(label, fv(a), fv(p), fv(r)))

    row("Signals (N)",       m_all["n"],      m1["n"],      m2["n"])
    row("Win Rate",          m_all["wr"],     m1["wr"],     m2["wr"],     "pct")
    row("Profit Factor",     m_all["pf"],     m1["pf"],     m2["pf"],     "f2")
    row("Total R",           m_all["totalr"], m1["totalr"], m2["totalr"], "r")
    row("Avg pct done",      m_all["avg_pct"],m1["avg_pct"],m2["avg_pct"],"pct")
    # Bucket breakdown
    print("-" * W)
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        def bpct(trades, b=bk):
            return sum(1 for t in trades if t.get("bucket") == b) / max(len(trades),1)*100
        print(hdr.format(
            f"  {bk}",
            f"{bpct(selected):.1f}%", f"{bpct(top1):.1f}%", f"{bpct(top2):.1f}%",
        ))
    lv_all = m_all["late"] + m_all["vlate"]
    lv1    = m1["late"] + m1["vlate"]
    lv2    = m2["late"] + m2["vlate"]
    print("-" * W)
    print(hdr.format("Late+VeryLate",
                     f"{lv_all:.1f}%", f"{lv1:.1f}%", f"{lv2:.1f}%"))

    # Avg rank score
    avg_s_all = _safe_avg([t["rank_score"] for t in selected])
    avg_s1    = _safe_avg([t["rank_score"] for t in top1])
    avg_s2    = _safe_avg([t["rank_score"] for t in top2])
    print(hdr.format("Avg rank score",
                     f"{avg_s_all:.1f}", f"{avg_s1:.1f}", f"{avg_s2:.1f}"))
    print("=" * W)


def _print_per_symbol(selected: List[Dict]) -> None:
    W = 80
    print("\n" + "=" * W)
    print("  PER-SYMBOL  (selected trades only)")
    print("=" * W)
    hdr = "{:<7}  {:>5}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}  {:>8}"
    print(hdr.format("Symbol", "N", "WR%", "PF", "TotalR", "LV%", "AvgPct", "AvgScore"))
    print("-" * W)
    for sym in SYMBOLS:
        st  = [t for t in selected if t["symbol"] == sym]
        m   = _metrics(st)
        avgs = _safe_avg([t["rank_score"] for t in st])
        lv   = m["late"] + m["vlate"]
        if m["n"] == 0:
            print(hdr.format(sym, 0, "-", "-", "-", "-", "-", "-"))
        else:
            print(hdr.format(sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                             f"{m['totalr']:+.1f}R", f"{lv:.0f}%",
                             f"{m['avg_pct']:.1f}%", f"{avgs:.1f}"))
    print("=" * W)


def _print_bucket_wr(pool: List[Dict], selected: List[Dict]) -> None:
    W = 72
    print("\n" + "=" * W)
    print("  WR BY BUCKET  (pool all candidates vs daily selected)")
    print("=" * W)
    hdr = "{:<16}  {:>12}  {:>7}  {:>8}  |  {:>12}  {:>7}  {:>8}"
    print(hdr.format("Bucket", "Pool:Count", "Pool:WR", "Pool:AvgR",
                     "Sel:Count", "Sel:WR", "Sel:AvgR"))
    print("-" * W)
    np_ = max(len(pool), 1); ns = max(len(selected), 1)
    for bk in ["Early", "Mid", "Late", "VeryLate"]:
        pp = [t for t in pool     if t.get("bucket") == bk]
        ps = [t for t in selected if t.get("bucket") == bk]
        mp = _metrics(pp); ms = _metrics(ps)
        ap = _safe_avg([t["r"] for t in pp])
        as_ = _safe_avg([t["r"] for t in ps])
        print(hdr.format(
            bk,
            f"{len(pp)} ({len(pp)/np_*100:.0f}%)", f"{mp['wr']:.1f}%", f"{ap:+.2f}R",
            f"{len(ps)} ({len(ps)/ns*100:.0f}%)", f"{ms['wr']:.1f}%", f"{as_:+.2f}R",
        ))
    print("=" * W)


def _write_csv(pool: List[Dict], selected: List[Dict]) -> None:
    pool_fields = [
        "symbol", "date", "time", "direction",
        "rank_score", "birth_age", "bars_after_birth",
        "b_qual", "disp_qual", "zone_fresh", "confluence", "htf",
        "sc_fresh", "sc_bq", "sc_dq", "sc_zf", "sc_conf", "sc_htf", "sc_ses",
        "pct_done", "bucket", "result", "r", "mfe_r", "mae_r",
    ]
    with open("backtest_x3_daily_pool.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=pool_fields)
        w.writeheader()
        for row in pool:
            w.writerow({k: row.get(k, "") for k in pool_fields})

    sel_fields = pool_fields + ["selection_rank"]
    with open("backtest_x3_daily_selected.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=sel_fields)
        w.writeheader()
        for row in selected:
            w.writerow({k: row.get(k, "") for k in sel_fields})

    print(f"\n  Pool CSV     -> backtest_x3_daily_pool.csv      ({len(pool)} rows)")
    print(f"  Selected CSV -> backtest_x3_daily_selected.csv  ({len(selected)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 84)
    print("  X3 DAILY SELECTION ENGINE -- Diagnostic Backtest")
    print(f"  Symbols       : {', '.join(SYMBOLS)}")
    print(f"  Window        : {DAYS} days")
    print(f"  Pre-screen    : birth mandatory  bq>={PRE_B_QUAL_MIN}"
          f"  dq>={PRE_DISP_QUAL_MIN}  zf>={PRE_ZONE_FRESH_MIN}"
          f"  age<={PRE_BIRTH_AGE_MAX}")
    print(f"  Selection     : top-1 + top-2 (diff symbols), floor={MIN_SELECT_SCORE}")
    print(f"  Pool cooldown : {POOL_COOLDOWN} bars per symbol per direction")
    print(f"  Run at        : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 84 + "\n")

    pool: List[Dict]      = []
    trading_dates: Set[str] = set()

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP")
            continue
        c15_all, c1h_all = dl

        # Collect all trading dates from the first successful download
        if not trading_dates:
            trading_dates = {c.timestamp.strftime("%Y-%m-%d")
                             for c in c15_all[MIN_HISTORY:]}

        cands = _build_pool(sym, c15_all, c1h_all)
        pool.extend(cands)
        wins = sum(1 for c in cands if c["result"] == "WIN")
        print(f"OK  {len(cands):>3} candidates  "
              f"WR={wins/max(len(cands),1)*100:.0f}%")

    if not pool:
        print("\nNo candidates generated."); return

    print(f"\n  Total pool : {len(pool)} candidates across {len(trading_dates)} trading days")

    # Phase 3: daily selection
    selected, day_counts = _daily_select(pool)

    # ── Reports ──────────────────────────────────────────────────────────────
    _print_decile_analysis(pool)
    _print_pool_stats(pool, trading_dates)
    _print_daily_coverage(selected, day_counts, trading_dates)
    _print_selection_results(selected)
    _print_per_symbol(selected)
    _print_bucket_wr(pool, selected)
    _write_csv(pool, selected)

    # Final summary
    m = _metrics(selected)
    m1 = _metrics([t for t in selected if t.get("selection_rank") == 1])
    lv = m["late"] + m["vlate"]
    print(f"\n  {'-'*80}")
    print(f"  Pool (all)   : N={len(pool)}  WR={_metrics(pool)['wr']:.1f}%"
          f"  PF={_metrics(pool)['pf']:.2f}  TotalR={_metrics(pool)['totalr']:+.1f}R")
    print(f"  Selected     : N={m['n']}  WR={m['wr']:.1f}%"
          f"  PF={m['pf']:.2f}  TotalR={m['totalr']:+.1f}R  LV={lv:.0f}%")
    print(f"  Top-1 only   : N={m1['n']}  WR={m1['wr']:.1f}%"
          f"  PF={m1['pf']:.2f}  TotalR={m1['totalr']:+.1f}R")
    trades_per_day = len(selected) / max(len(trading_dates), 1)
    top1_per_day   = m1["n"] / max(len(trading_dates), 1)
    print(f"  Coverage     : {trades_per_day:.2f} trades/day"
          f"  ({top1_per_day:.2f} top-1/day)")
    print(f"  {'-'*80}")


if __name__ == "__main__":
    main()
