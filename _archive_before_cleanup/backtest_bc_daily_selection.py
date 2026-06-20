# -*- coding: utf-8 -*-
"""
backtest_bc_daily_selection.py  --  Daily Best-Trade Selection on B+C + atr_pct<=0.52
======================================================================================
Approved 2026-06-20.

Tests whether selecting the top-N B+C signals per day (ranked by the
existing research-grounded rank_score) materially improves quality.

Engine:
  B + C + atr_pct <= 0.52  (exact, not optimized)
  All B+C constants unchanged from backtest_bc_research.py.

Ranking:
  Uses _rank_score imported UNCHANGED from backtest_daily_selection.py.
  Formula: HTF(30) + BirthFresh(30) + Session(20) + Extension(9) + Confluence(10) + BQ(5)
  No new score created.

Variants:
  A)  All B+C signals passing the filter   (baseline)
  B)  Top-3 per day by rank_score
  C)  Top-2 per day by rank_score
  D)  Top-1 per day by rank_score

Reports:
  - A/B/C/D overall: N, WR, PF, TotalR, AvgMFE, AvgMAE, LV%
  - Score decile table (all filtered pool)
  - By symbol: N, WR, PF, TotalR
  - Leave-one-out: Top-1 and Top-2
  - Comparison table: All vs Top-2 vs Top-1

16 symbols, yfinance DOWNLOAD_DAYS=55 (same as validated B+C study).
Do not modify analyzer_x2.  Research only.
"""
from __future__ import annotations

import sys
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance"); sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from,
        _detect_zone, _zone_bounds, _htf_direction,
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
        SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
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

# ── Import rank score UNCHANGED from daily selection study ────────────────────
try:
    from backtest_daily_selection import _rank_score, _session_pts, N_DECILES
except Exception as e:
    print(f"Cannot import backtest_daily_selection: {e}"); sys.exit(1)


# ── Constants (identical to backtest_bc_research.py -- do not change) ─────────

DOWNLOAD_DAYS      = 55
CONF_START         = 2
CONF_END           = 6
POOL_COOLDOWN      = 16
PRE_B_QUAL_MIN     = 0.55
PRE_DISP_QUAL_MIN  = 0.35
PRE_BIRTH_AGE_MAX  = 15
HTF_MIN_STRENGTH   = 0.30
SESSION_CUTOFF     = 525
STOP_BUFFER        = 0.22
DISPLACE_BODY_MIN  = 0.50
DISPLACE_CLOSE_MIN = 0.65

ATR_THRESHOLD      = 0.52   # filter applied BEFORE daily ranking

SYMBOLS: List[str] = [
    "AAPL", "AMD",  "TSLA", "AVGO", "COST", "LLY",  "PANW", "CRM",
    "QQQ",  "SPY",  "MSFT", "META", "AMZN", "GOOGL", "NVDA", "NFLX",
]

W = 90


# ── EMA helper ────────────────────────────────────────────────────────────────

def _ema(prices: List[float], period: int) -> float:
    if len(prices) < period:
        return prices[-1] if prices else 0.0
    k   = 2.0 / (period + 1)
    val = sum(prices[:period]) / period
    for p in prices[period:]:
        val = p * k + val * (1 - k)
    return val


# ── Download ──────────────────────────────────────────────────────────────────

def _download(symbol: str) -> Optional[Tuple[List[Candle], List[Candle]]]:
    end   = datetime.today()
    start = end - timedelta(days=DOWNLOAD_DAYS + 3)
    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  {e}"); return None
    if df15 is None or len(df15) < MIN_HISTORY:
        return None
    c15 = _to_candles(df15)
    c1h = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []
    return c15, c1h


# ── B+C scanner with rank_score and confluence ────────────────────────────────
# Logic identical to backtest_bc_research._scan_symbol.
# ADDED: rank_score, confluence, atr_pct, price_ext_atr at B+C fire point.

def _scan_bc_ranked(
    symbol:  str,
    c15_all: List[Candle],
    c1h_all: List[Candle],
) -> List[Dict[str, Any]]:

    prof    = _get_profile(symbol)
    min_adx = float(prof.get("min_adx", 17))
    max_adx = float(prof.get("max_adx", 68))

    results: List[Dict] = []
    last_birth: Dict[str, int] = {"LONG": -999, "SHORT": -999}
    safe_max = len(c15_all) - SIM_BARS - CONF_END - 2

    for i in range(MIN_HISTORY, safe_max):
        c15 = c15_all[: i + 1]
        ts  = c15_all[i].timestamp        # birth bar timestamp (UTC)

        zone_setup = c15[-220:]
        if len(zone_setup) < 60:
            continue

        market = _market_state_from(zone_setup, c15_all[i].close)
        atr    = market.atr_14
        adx    = market.adx
        if adx < min_adx or adx > max_adx:
            continue

        c1h      = [c for c in c1h_all if c.timestamp <= ts] or c15[-40:]
        htf_feed = c1h[-300:] if len(c1h) >= 60 else c15[-220:]
        htf      = _htf_direction(htf_feed, market)
        dirs     = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:

            sw_price, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            swing_local  = len(c15) - 1 - bars_since
            swing_global = i - bars_since

            b_exp = _cap(_birth_expand(c15, swing_local, direction, atr), bars_since)
            b_vol = _cap(_birth_volume(c15, swing_local),                  bars_since)
            b_str = _cap(_birth_structure(c15, swing_local, direction),    bars_since)
            b_any = min(b_exp, b_vol, b_str)

            birth_global     = swing_global + b_any
            bars_after_birth = i - birth_global

            if bars_after_birth != 0:
                continue
            if b_any > PRE_BIRTH_AGE_MAX:
                continue
            if birth_global <= last_birth[direction]:
                continue
            if not (0 <= birth_global < len(c15_all)):
                continue

            bc     = c15_all[birth_global]
            b_body = abs(bc.close - bc.open)
            b_qual = min(1.0, b_body / max(atr, 1e-9))
            if b_qual < PRE_B_QUAL_MIN:
                continue

            b_local = swing_local + b_any
            try:
                disp_qual = _disp_quality(c15, b_local, direction, atr)
            except Exception:
                disp_qual = 0.0
            if disp_qual < PRE_DISP_QUAL_MIN:
                continue

            h_src    = c1h if len(c1h) >= 50 else c15[-100:]
            h_prices = [c.close for c in h_src]
            raw_htf  = (((_ema(h_prices, 20) - _ema(h_prices, 50))
                         / max(abs(_ema(h_prices, 50)), 1e-9)) * 100.0)
            htf_str  = raw_htf if direction == "LONG" else -raw_htf
            if htf_str <= HTF_MIN_STRENGTH:
                continue

            h_u, m_u    = ts.hour, ts.minute
            session_min = (h_u - 9) * 60 + m_u - 30
            if session_min >= SESSION_CUTOFF:
                continue

            near    = b_any + 2
            b1_fire = b_exp <= near
            b2_fire = b_vol <= near
            b3_fire = b_str <= near
            if not (b1_fire or b2_fire or b3_fire):
                continue
            confluence = int(b1_fire) + int(b2_fire) + int(b3_fire)

            last_birth[direction] = birth_global

            zone = _detect_zone(zone_setup, direction, atr,
                                max(0, len(zone_setup) - 1 - bars_since))
            if zone:
                zb = _zone_bounds(zone)
                if zb:
                    z_top, z_bot = zb
                    stop_price = (z_bot - STOP_BUFFER * atr if direction == "LONG"
                                  else z_top + STOP_BUFFER * atr)
                else:
                    stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                                  else bc.high + STOP_BUFFER * atr)
            else:
                stop_price = (bc.low  - STOP_BUFFER * atr if direction == "LONG"
                              else bc.high + STOP_BUFFER * atr)

            birth_area = c15_all[swing_global : birth_global + 1]
            if not birth_area:
                continue
            if direction == "LONG":
                h_struct = max(c.high for c in birth_area)
            else:
                l_struct = min(c.low  for c in birth_area)

            # ── Confirmation window +2..+6 ────────────────────────────────
            b_offset: Optional[int] = None
            c_offset: Optional[int] = None

            for offset in range(CONF_START, CONF_END + 1):
                conf_idx = birth_global + offset
                if conf_idx >= safe_max + CONF_END:
                    break
                cb2 = c15_all[conf_idx]
                rng = max(cb2.high - cb2.low, 1e-9)

                if direction == "LONG":
                    body2     = cb2.close - cb2.open
                    dir_close = (cb2.close - cb2.low) / rng
                    if b_offset is None:
                        if (cb2.close > cb2.open
                                and body2     >= DISPLACE_BODY_MIN  * atr
                                and dir_close >= DISPLACE_CLOSE_MIN):
                            b_offset = offset
                    if c_offset is None and cb2.close > h_struct:
                        c_offset = offset
                else:
                    body2     = cb2.open - cb2.close
                    dir_close = (cb2.high - cb2.close) / rng
                    if b_offset is None:
                        if (cb2.close < cb2.open
                                and body2     >= DISPLACE_BODY_MIN  * atr
                                and dir_close >= DISPLACE_CLOSE_MIN):
                            b_offset = offset
                    if c_offset is None and cb2.close < l_struct:
                        c_offset = offset

                if b_offset is not None and c_offset is not None:
                    entry_offset = max(b_offset, c_offset)
                    entry_idx    = birth_global + entry_offset
                    entry_px     = c15_all[entry_idx].close

                    # ── atr_pct (regime filter) ────────────────────────────
                    atr_pct = atr / max(entry_px, 1e-9) * 100.0

                    # ── price_ext_atr (for rank_score extension component) ──
                    if direction == "LONG":
                        price_ext_atr = (entry_px - bc.close) / max(atr, 1e-9)
                    else:
                        price_ext_atr = (bc.close - entry_px) / max(atr, 1e-9)
                    price_ext_atr = max(0.0, price_ext_atr)

                    # ── Rank score (existing formula, unchanged) ────────────
                    rank, _ = _rank_score(
                        htf_strength  = htf_str,
                        birth_age     = b_any,
                        ts            = ts,           # birth bar timestamp
                        price_ext_atr = price_ext_atr,
                        confluence    = confluence,
                        b_qual        = b_qual,
                    )

                    # ── Simulation ─────────────────────────────────────────
                    risk = abs(entry_px - stop_price)
                    if risk <= 1e-9:
                        break
                    tp1    = (entry_px + risk * TP1_R if direction == "LONG"
                              else entry_px - risk * TP1_R)
                    result = "BE"; r_mult = 0.0
                    mfe = 0.0; mae = 0.0
                    for fc in c15_all[entry_idx + 1 : entry_idx + SIM_BARS + 1]:
                        if direction == "LONG":
                            mfe = max(mfe, (fc.high - entry_px) / risk)
                            mae = min(mae, (fc.low  - entry_px) / risk)
                            if fc.low  <= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.high >= tp1:        result="WIN";  r_mult=TP1_R; break
                        else:
                            mfe = max(mfe, (entry_px - fc.low)  / risk)
                            mae = min(mae, (entry_px - fc.high) / risk)
                            if fc.high >= stop_price: result="LOSS"; r_mult=-1.0; break
                            if fc.low  <= tp1:        result="WIN";  r_mult=TP1_R; break

                    bars_sw = entry_idx - swing_global
                    future  = c15_all[entry_idx + 1 : entry_idx + 1 + SWING_LOOKFWD]
                    sw_end  = _swing_end(future, direction)
                    if sw_end is not None:
                        total_mv = ((sw_end - sw_price) if direction == "LONG"
                                    else (sw_price - sw_end))
                        at_entry = ((entry_px - sw_price) if direction == "LONG"
                                    else (sw_price - entry_px))
                        pct = round(at_entry / total_mv * 100, 1) if total_mv > 0 else 50.0
                    else:
                        pct = 50.0

                    seq = ("B_then_C" if b_offset < c_offset else
                           ("C_then_B" if c_offset < b_offset else "same_bar"))

                    results.append({
                        "symbol":       symbol,
                        "direction":    "CALL" if direction == "LONG" else "PUT",
                        "date":         ts.strftime("%Y-%m-%d"),
                        "time":         ts.strftime("%H:%M"),
                        "entry_offset": entry_offset,
                        "sequence":     seq,
                        "bars_since_swing": bars_sw,
                        "pct_done":     pct,
                        "bucket":       _bucket(pct),
                        "result":       result,
                        "r":            round(r_mult, 2),
                        "mfe_r":        round(mfe, 2),
                        "mae_r":        round(mae, 2),
                        # filter field
                        "atr_pct":      round(atr_pct, 4),
                        # score fields
                        "rank_score":   rank,
                        "confluence":   confluence,
                        "htf_strength": round(htf_str,        3),
                        "birth_age":    b_any,
                        "b_qual":       round(b_qual,         3),
                        "price_ext_atr":round(price_ext_atr,  3),
                        "session_min":  session_min,
                    })
                    break

    return results


# ── Daily selection ───────────────────────────────────────────────────────────

def _daily_select(pool: List[Dict], top_n: Optional[int]) -> List[Dict]:
    if top_n is None:
        return list(pool)
    by_date: Dict[str, List[Dict]] = defaultdict(list)
    for t in pool:
        by_date[t["date"]].append(t)
    selected: List[Dict] = []
    for date in sorted(by_date):
        ranked = sorted(by_date[date], key=lambda c: -c["rank_score"])
        selected.extend(ranked[:top_n])
    return selected


# ── Metrics ───────────────────────────────────────────────────────────────────

def _m(trades: List[Dict]) -> Dict[str, Any]:
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "totalr": 0.0,
                "mfe": 0.0, "mae": 0.0, "lv": 0.0}
    wins = sum(1 for t in trades if t["result"] == "WIN")
    loss = sum(1 for t in trades if t["result"] == "LOSS")
    gw   = sum(float(t["r"]) for t in trades if float(t["r"]) > 0)
    gl   = abs(sum(float(t["r"]) for t in trades if float(t["r"]) < 0))
    dec  = wins + loss
    lv   = sum(1 for t in trades if t.get("bucket") in ("Late", "VeryLate"))
    return {
        "n":      n,
        "wr":     wins / dec * 100 if dec else 0.0,
        "pf":     gw / gl if gl > 0 else (99.0 if gw > 0 else 0.0),
        "totalr": sum(float(t["r"]) for t in trades),
        "mfe":    _safe_avg([float(t["mfe_r"]) for t in trades]),
        "mae":    _safe_avg([float(t["mae_r"]) for t in trades]),
        "lv":     lv / n * 100,
    }

def _hline(c: str = "-") -> str:
    return "  " + c * (W - 2)


# ── Reports ───────────────────────────────────────────────────────────────────

def _print_variants(
    all_filt: List[Dict],
    top3:     List[Dict],
    top2:     List[Dict],
    top1:     List[Dict],
) -> None:
    print("\n" + "=" * W)
    print("  VARIANT COMPARISON  --  A) All  B) Top-3  C) Top-2  D) Top-1")
    print(f"  Pool = B+C + atr_pct<={ATR_THRESHOLD}  |  Ranked by existing rank_score")
    print("=" * W)
    hdr = "  {:<26}  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}  {:>8}  {:>6}"
    print(hdr.format("Variant", "N", "WR%", "PF", "TotalR", "AvgMFE", "AvgMAE", "LV%"))
    print(_hline())
    for label, grp in [
        ("A) All (baseline)", all_filt),
        ("B) Top-3 per day",  top3),
        ("C) Top-2 per day",  top2),
        ("D) Top-1 per day",  top1),
    ]:
        m = _m(grp)
        print(hdr.format(
            label, m["n"],
            f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R",
            f"{m['mae']:+.2f}R", f"{m['lv']:.1f}%",
        ))
    print("=" * W)


def _print_score_deciles(pool: List[Dict]) -> None:
    if not pool:
        return
    sp  = sorted(pool, key=lambda c: c["rank_score"])
    n   = len(sp)
    sz  = max(1, n // N_DECILES)

    print("\n" + "=" * W)
    print(f"  SCORE DECILE TABLE  --  {n} filtered candidates")
    print(f"  Formula: HTF(30) + BirthFresh(30) + Session(20) + Extension(9) + Confluence(10) + BQ(5)")
    print(f"  Proof: if rank_score predicts outcome, WR/PF should rise monotonically with decile.")
    print("=" * W)
    hdr = "  {:>6}  {:>11}  {:>5}  {:>6}  {:>5}  {:>8}  {:>8}"
    print(hdr.format("Decile", "ScoreRange", "N", "WR%", "PF", "TotalR", "AvgMFE"))
    print(_hline())

    for d in range(N_DECILES):
        lo  = d * sz
        hi  = (d + 1) * sz if d < N_DECILES - 1 else n
        grp = sp[lo:hi]
        if not grp:
            continue
        m    = _m(grp)
        s_lo = grp[0]["rank_score"]
        s_hi = grp[-1]["rank_score"]
        print(hdr.format(
            f"D{d+1:02d}",
            f"{s_lo:.1f}-{s_hi:.1f}",
            m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
            f"{m['totalr']:+.1f}R", f"{m['mfe']:+.2f}R",
        ))

    print(_hline())
    ma = _m(pool)
    print(hdr.format(
        "ALL",
        f"{sp[0]['rank_score']:.1f}-{sp[-1]['rank_score']:.1f}",
        ma["n"], f"{ma['wr']:.1f}%", f"{ma['pf']:.2f}",
        f"{ma['totalr']:+.1f}R", f"{ma['mfe']:+.2f}R",
    ))
    print("=" * W)

    # Quick monotonicity test
    decile_pf = []
    for d in range(N_DECILES):
        lo  = d * sz
        hi  = (d + 1) * sz if d < N_DECILES - 1 else n
        grp = sp[lo:hi]
        if grp:
            decile_pf.append(_m(grp)["pf"])

    # Count monotone rises
    rises = sum(1 for i in range(1, len(decile_pf)) if decile_pf[i] > decile_pf[i-1])
    print(f"\n  Monotonicity: {rises}/{len(decile_pf)-1} decile transitions show PF rising.")
    if rises >= len(decile_pf) * 0.6:
        print(f"  SIGNAL: score shows directional separation (>60% rising transitions).")
    else:
        print(f"  NO SIGNAL: score does not predict outcome in this filtered pool.")


def _print_by_symbol(label: str, trades: List[Dict]) -> None:
    print(f"\n  By symbol -- {label}  (N={len(trades)}):")
    hdr = "  {:<6}  {:>4}  {:>6}  {:>5}  {:>8}  {:>7}"
    print(hdr.format("Symbol", "N", "WR%", "PF", "TotalR", "AvgScore"))
    print(_hline())

    rows = []
    for sym in SYMBOLS:
        st = [t for t in trades if t["symbol"] == sym]
        if not st:
            continue
        m    = _m(st)
        avg_s = sum(t["rank_score"] for t in st) / len(st)
        rows.append((sym, m, avg_s))

    for sym, m, avg_s in sorted(rows, key=lambda x: -x[1]["totalr"]):
        print(hdr.format(sym, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R", f"{avg_s:.1f}"))

    print(_hline())
    mt = _m(trades)
    print(hdr.format("TOTAL", mt["n"], f"{mt['wr']:.1f}%", f"{mt['pf']:.2f}",
                      f"{mt['totalr']:+.1f}R", "-"))
    print("=" * W)


def _print_leave_one_out(label: str, trades: List[Dict]) -> None:
    fm = _m(trades)
    if fm["n"] == 0:
        print(f"\n  Leave-one-out [{label}]: no trades."); return
    print(f"\n" + "=" * W)
    print(f"  LEAVE-ONE-OUT  --  {label}")
    print(f"  Full: N={fm['n']}  WR={fm['wr']:.1f}%  PF={fm['pf']:.2f}  "
          f"TotalR={fm['totalr']:+.1f}R")
    print(f"  dPF negative = symbol was helping.")
    print("=" * W)

    hdr = "  {:<6}  {:>4}  {:>6}  {:>5}  {:>8}  {:>7}  {:>7}"
    print(hdr.format("Removed", "N", "WR%", "PF", "TotalR", "dWR", "dPF"))
    print(_hline())

    rows = [(sym, _m([t for t in trades if t["symbol"] != sym])) for sym in SYMBOLS]
    rows.sort(key=lambda x: x[1]["pf"] - fm["pf"])

    for sym, sm in rows:
        sym_n = sum(1 for t in trades if t["symbol"] == sym)
        if sym_n == 0:
            continue
        d_pf = sm["pf"] - fm["pf"]
        d_wr = sm["wr"] - fm["wr"]
        flag = ""
        if d_pf <= -0.35: flag = "  <-- carrying result"
        elif d_pf >= +0.35: flag = "  <-- dragging result"
        print(hdr.format(sym, sm["n"], f"{sm['wr']:.1f}%", f"{sm['pf']:.2f}",
                          f"{sm['totalr']:+.1f}R",
                          f"{d_wr:+.1f}pp", f"{d_pf:+.2f}") + flag)

    # Concentration check
    top_r   = max((_m([t for t in trades if t["symbol"] == s])["totalr"]
                   for s in SYMBOLS), default=0)
    top_sym = max(SYMBOLS, key=lambda s: _m([t for t in trades if t["symbol"] == s])["totalr"])
    tot_r   = fm["totalr"]
    print()
    if tot_r > 0 and top_r > 0:
        pct = top_r / tot_r * 100
        if pct > 60:
            print(f"  CONCENTRATION: {top_sym} = {pct:.0f}% of TotalR -- concentrated")
        elif pct > 40:
            print(f"  MODERATE: {top_sym} = {pct:.0f}% of TotalR")
        else:
            print(f"  DISTRIBUTED: top sym ({top_sym}) = {pct:.0f}% of TotalR")
    else:
        print(f"  TotalR <= 0 -- selection variant not beneficial here")
    print("=" * W)


def _print_final_comparison(
    all_filt: List[Dict],
    top2:     List[Dict],
    top1:     List[Dict],
) -> None:
    print("\n" + "=" * W)
    print("  FINAL COMPARISON  --  does daily selection create meaningful edge?")
    print("=" * W)
    hdr = "  {:<26}  {:>4}  {:>6}  {:>5}  {:>8}  {:>6}"
    print(hdr.format("Variant", "N", "WR%", "PF", "TotalR", "LV%"))
    print(_hline())
    for label, grp in [
        ("All B+C filtered", all_filt),
        ("Top-2 per day",    top2),
        ("Top-1 per day",    top1),
    ]:
        m = _m(grp)
        print(hdr.format(label, m["n"], f"{m['wr']:.1f}%", f"{m['pf']:.2f}",
                          f"{m['totalr']:+.1f}R", f"{m['lv']:.1f}%"))

    # Verdict
    m_all = _m(all_filt)
    m_t1  = _m(top1)
    m_t2  = _m(top2)
    d_pf_t2 = m_t2["pf"] - m_all["pf"]
    d_pf_t1 = m_t1["pf"] - m_all["pf"]

    print()
    print(f"  Top-2 vs All: dPF={d_pf_t2:+.2f}  dN={m_t2['n']-m_all['n']:+d}  "
          f"dTotalR={m_t2['totalr']-m_all['totalr']:+.1f}R")
    print(f"  Top-1 vs All: dPF={d_pf_t1:+.2f}  dN={m_t1['n']-m_all['n']:+d}  "
          f"dTotalR={m_t1['totalr']-m_all['totalr']:+.1f}R")
    print()

    has_edge = d_pf_t1 >= 0.20 or d_pf_t2 >= 0.20
    if has_edge:
        print(f"  VERDICT: daily selection IMPROVES PF (>= 0.20 gain on Top-1 or Top-2).")
        print(f"  Requires leave-one-out confirmation to exclude concentration effect.")
    elif d_pf_t1 < 0:
        print(f"  VERDICT: daily selection HURTS PF. Score is anti-predictive in this pool.")
    else:
        print(f"  VERDICT: daily selection produces NEGLIGIBLE change (< 0.20 PF gain).")
        print(f"  Score does not create meaningful edge on the B+C filtered pool.")
    print("=" * W)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * W)
    print("  B+C DAILY SELECTION STUDY")
    print(f"  Engine  : B + C + atr_pct <= {ATR_THRESHOLD}")
    print(f"  Score   : _rank_score from backtest_daily_selection (unchanged)")
    print(f"  Symbols : {len(SYMBOLS)}")
    print(f"  Run     : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * W + "\n")

    # ── Download and scan ──────────────────────────────────────────────────────
    all_raw: List[Dict] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] downloading ... ", end="", flush=True)
        dl = _download(sym)
        if dl is None:
            print("SKIP"); continue
        c15_all, c1h_all = dl

        trades = _scan_bc_ranked(sym, c15_all, c1h_all)
        all_raw.extend(trades)

        wins = sum(1 for t in trades if t["result"] == "WIN")
        loss = sum(1 for t in trades if t["result"] == "LOSS")
        n_f  = sum(1 for t in trades if t["atr_pct"] <= ATR_THRESHOLD)
        print(f"OK  {len(trades):>3} BC  WR={wins/max(wins+loss,1)*100:.0f}%  "
              f"filt={n_f}/{len(trades)}")

    if not all_raw:
        print("\nNo trades."); return

    # ── Apply filter ───────────────────────────────────────────────────────────
    all_filt = [t for t in all_raw if t["atr_pct"] <= ATR_THRESHOLD]
    print(f"\n  Total raw: {len(all_raw)}  |  After atr_pct<={ATR_THRESHOLD}: {len(all_filt)}")

    if not all_filt:
        print("  No filtered trades."); return

    # ── Daily selection variants ───────────────────────────────────────────────
    top3 = _daily_select(all_filt, 3)
    top2 = _daily_select(all_filt, 2)
    top1 = _daily_select(all_filt, 1)

    print(f"  Daily selection: Top-3={len(top3)}  Top-2={len(top2)}  Top-1={len(top1)}")

    # ── Reports ────────────────────────────────────────────────────────────────
    _print_variants(all_filt, top3, top2, top1)
    _print_score_deciles(all_filt)
    _print_by_symbol("All filtered (A)", all_filt)
    _print_by_symbol("Top-2 per day (C)", top2)
    _print_by_symbol("Top-1 per day (D)", top1)
    _print_leave_one_out("Top-2 per day", top2)
    _print_leave_one_out("Top-1 per day", top1)
    _print_final_comparison(all_filt, top2, top1)


if __name__ == "__main__":
    main()
