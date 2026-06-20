# -*- coding: utf-8 -*-
"""
backtest_move_birth.py — SMART ICT X2: Move Birth Detector  (prototype diagnostic)
===================================================================================
Measures three early-signal anchors that appear immediately after the swing
extreme, then reports how far ahead of the current ICT pipeline each anchor fires.

Birth anchors (bars from swing extreme, 15m candles):
  B1 — Expansion Candle : first bull/bear candle with body >= 0.8 ATR in direction
  B2 — Volume Expansion : first candle with volume >= 1.3x prior 20-bar average
  B3 — Structure Break  : first close that breaks the local 5-bar swing level

Pipeline reported per accepted trade:
  SwingStart -[B_bars]-> MoveBirth -[birth_to_sweep]-> Sweep
             -[S2]-> Displacement -[S3]-> Zone -[S4]-> Entry

Also computes the THEORETICAL entry timing (price and pct_done at birth + 7 bars)
to estimate whether earlier anchoring shifts signals from Late/VeryLate into Mid.

Diagnostics only. No strategy changes.
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import csv

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
except ImportError:
    print("pip install yfinance")
    sys.exit(1)

try:
    from backtest_runner_x2 import (
        _flat, _to_candles, _market_state_from, _htf_direction,
        _call_detect_sweep, _call_detect_displacement,
        _detect_zone, _is_retest, _zone_bounds, _zone_mid, _event_index,
        _dir_enum,
        TP1_R, SETUP_COOLDOWN, MIN_HISTORY,
    )
except Exception as e:
    print(f"Cannot import backtest_runner_x2: {e}")
    sys.exit(1)

try:
    from analyzer_x2 import (
        Candle, Direction, SYMBOL_PROFILES, _volume_ratio,
        _fresh_zone_qualifies,
    )
except Exception as e:
    print(f"Cannot import analyzer_x2: {e}")
    sys.exit(1)

try:
    from backtest_entry_timing import (
        _get_profile, _swing_extreme, _swing_end, _bucket, _safe_avg,
        SYMBOLS, DAYS, SWING_LOOKBACK, SWING_LOOKFWD, SIM_BARS,
    )
except Exception as e:
    print(f"Cannot import backtest_entry_timing: {e}")
    sys.exit(1)

# Pipeline minimum (S3+S4 average from Table E): 7 bars from displacement to entry
PIPELINE_MIN_BARS = 7


# ── Birth Anchor Detection ────────────────────────────────────────────────────

def _birth_expand(candles: List[Candle], swing_idx: int,
                  direction: str, atr: float) -> Optional[int]:
    """First directional candle with body >= 0.8 ATR after swing_idx."""
    if atr <= 0 or swing_idx >= len(candles) - 1:
        return None
    for j in range(swing_idx + 1, len(candles)):
        c = candles[j]
        body = abs(c.close - c.open)
        if direction == "LONG"  and c.close > c.open and body >= atr * 0.8:
            return j - swing_idx
        if direction == "SHORT" and c.close < c.open and body >= atr * 0.8:
            return j - swing_idx
    return None


def _birth_volume(candles: List[Candle], swing_idx: int) -> Optional[int]:
    """First candle with volume >= 1.3x prior 20-bar average after swing_idx."""
    pre  = candles[max(0, swing_idx - 20):swing_idx]
    vols = [c.volume for c in pre if c.volume and c.volume > 0]
    if len(vols) < 5:
        return None
    avg_vol = sum(vols) / len(vols)
    if avg_vol <= 0:
        return None
    for j in range(swing_idx + 1, len(candles)):
        c = candles[j]
        if c.volume and c.volume > 0 and c.volume >= avg_vol * 1.3:
            return j - swing_idx
    return None


def _birth_structure(candles: List[Candle], swing_idx: int,
                     direction: str, lookback: int = 5) -> Optional[int]:
    """
    First close that breaks the local structure level after swing_idx.
    LONG:  close > max(high) of the 5 bars ending at swing_idx
    SHORT: close < min(low)  of the 5 bars ending at swing_idx
    """
    pre = candles[max(0, swing_idx - lookback + 1):swing_idx + 1]
    if not pre:
        return None
    if direction == "LONG":
        level = max(c.high for c in pre)
        for j in range(swing_idx + 1, len(candles)):
            if candles[j].close > level:
                return j - swing_idx
    else:
        level = min(c.low for c in pre)
        for j in range(swing_idx + 1, len(candles)):
            if candles[j].close < level:
                return j - swing_idx
    return None


def _cap(val: Optional[int], ceiling: int) -> int:
    """Cap a birth bar count at the entry bar (bars_since) if not found earlier."""
    if val is None or val > ceiling:
        return ceiling
    return val


# ── Per-symbol scan ───────────────────────────────────────────────────────────

def _run_birth_symbol(symbol: str) -> List[Dict[str, Any]]:
    end   = datetime.today()
    start = end - timedelta(days=DAYS + 3)

    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m",
                                  progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h",
                                  progress=False, auto_adjust=True))
    except Exception as e:
        print(f"  download error: {e}")
        return []

    if df15 is None or len(df15) < MIN_HISTORY:
        print(f"  insufficient data ({len(df15) if df15 is not None else 0} rows)")
        return []

    c15_all: List[Candle] = _to_candles(df15)
    c1h_all: List[Candle] = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

    prof     = _get_profile(symbol)
    tf       = prof.get("timeframe", "1H")
    min_adx  = float(prof.get("min_adx", 17))
    max_adx  = float(prof.get("max_adx", 68))
    min_conf = float(prof.get("min_conf", 72))

    trades: List[Dict[str, Any]] = []
    last_bar = -999

    for i in range(MIN_HISTORY, len(c15_all) - SIM_BARS - 1):
        c15 = c15_all[:i + 1]
        ts  = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        setup = (c15[-220:] if tf == "15m"
                 else (c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:]))
        trig  = c15[-8:]
        if len(setup) < 60:
            continue

        price  = c15_all[i].close
        market = _market_state_from(setup, price)
        atr    = market.atr_14
        adx    = market.adx

        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_bar) < SETUP_COOLDOWN:
            continue

        htf  = _htf_direction(c1h[-300:] if c1h else c15[-300:], market)
        dirs = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in dirs:
            sweep = _call_detect_sweep(setup, direction, atr)
            aidx  = _event_index(sweep) if sweep else max(0, len(setup) - 36)
            disp  = _call_detect_displacement(setup, direction, atr, aidx)

            if sweep is None and disp is None:
                continue

            zafter = _event_index(disp) if disp else aidx
            zone   = _detect_zone(setup, direction, atr, zafter)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb
            z_mid = _zone_mid(zone)

            _fz_ok, _ = _fresh_zone_qualifies(
                sweep, len(setup), disp, zone, price, atr, _dir_enum(direction)
            )
            if _fz_ok:
                entry_mode = "FRESH_ZONE_ENTRY"
            else:
                if abs(price - z_mid) / max(atr, 0.01) > 3.2:
                    continue
                if not _is_retest(trig, zone, direction, atr):
                    continue
                entry_mode = "RETEST_ENTRY"

            sq  = float(getattr(sweep, "quality", 0) or 0) if sweep else 0.0
            dq  = float(getattr(disp,  "quality", 0) or 0) if disp  else 0.0
            zq  = float(getattr(zone,  "quality", 0.7) or 0.7)
            fr  = float(getattr(zone,  "freshness", 0.7) or 0.7)
            try:
                vr = _volume_ratio(setup)
            except Exception:
                vr = 1.0
            score = 30 + sq*14 + dq*14 + zq*20 + fr*10
            if sweep and disp:  score += 6
            score += 5 if htf != "NEUTRAL" else 0
            score += 5 if vr >= 0.8 else 2
            if score < min_conf:
                continue

            entry = c15_all[i].close
            stop  = z_bot - atr*0.22 if direction == "LONG" else z_top + atr*0.22
            risk  = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk*TP1_R if direction == "LONG" else entry - risk*TP1_R

            # ── Swing timing ─────────────────────────────────────────
            sw_start, bars_since = _swing_extreme(c15, direction, SWING_LOOKBACK)
            future               = c15_all[i + 1 : i + 1 + SWING_LOOKFWD]
            sw_end               = _swing_end(future, direction)

            if direction == "LONG":
                total_mv = (sw_end - sw_start) if sw_end else None
                at_entry = entry - sw_start
            else:
                total_mv = (sw_start - sw_end) if sw_end else None
                at_entry = sw_start - entry

            pct = (round(at_entry / total_mv * 100, 1)
                   if (total_mv and total_mv > 0) else 50.0)

            # ── Birth anchor detection ───────────────────────────────
            swing_idx = len(c15) - 1 - bars_since

            b_expand_raw = _birth_expand(c15, swing_idx, direction, atr)
            b_vol_raw    = _birth_volume(c15, swing_idx)
            b_struct_raw = _birth_structure(c15, swing_idx, direction)

            # Cap each anchor at bars_since (can't fire after entry)
            b_expand = _cap(b_expand_raw, bars_since)
            b_vol    = _cap(b_vol_raw,    bars_since)
            b_struct = _cap(b_struct_raw, bars_since)
            b_any    = min(b_expand, b_vol, b_struct)

            # ── Theoretical entry at birth + PIPELINE_MIN_BARS ───────
            t_bar_idx = swing_idx + b_any + PIPELINE_MIN_BARS
            if total_mv and total_mv > 0 and t_bar_idx < len(c15):
                t_price = c15[t_bar_idx].close
                t_at    = (t_price - sw_start) if direction == "LONG" else (sw_start - t_price)
                t_pct   = round(t_at / total_mv * 100, 1)
                t_pct   = max(0.0, t_pct)
            else:
                t_pct = pct

            # ── Current pipeline stage timings (relative to setup window) ─
            _slen      = len(setup)
            _sweep_ago = (_slen - 1 - sweep.index) if sweep else bars_since
            _disp_ago  = (_slen - 1 - disp.index)  if disp  else _sweep_ago
            _zcix      = getattr(zone, "created_index", _slen - 1)
            _zone_ago  = _slen - 1 - _zcix

            # Birth-anchored gap to sweep:
            # birth fires (bars_since - b_any) bars before entry;
            # sweep detected _sweep_ago bars before entry
            birth_ago   = bars_since - b_any
            birth_to_sw = max(0, birth_ago - _sweep_ago)

            s2 = max(0, _sweep_ago - _disp_ago)
            s3 = max(0, _disp_ago  - _zone_ago)
            s4 = max(0, _zone_ago)

            # ── Outcome simulation ───────────────────────────────────
            outcome = "BE"; r_mult = 0.0; ep = entry; mfe = 0.0; mae = 0.0
            for j, fc in enumerate(c15_all[i + 1 : i + 1 + SIM_BARS + 1], 1):
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

            trades.append({
                "symbol":       symbol,
                "date":         ts.strftime("%Y-%m-%d"),
                "time":         ts.strftime("%H:%M"),
                "direction":    "CALL" if direction == "LONG" else "PUT",
                "entry":        round(entry, 4),
                "bars_since":   bars_since,
                "pct_done":     pct,
                "bucket":       _bucket(pct),
                "entry_mode":   entry_mode,
                "result":       outcome,
                "r":            round(r_mult, 2),
                "mfe_r":        round(mfe, 2),
                "mae_r":        round(mae, 2),
                # Birth anchors (bars from swing extreme; capped at bars_since)
                "b_expand":     b_expand,
                "b_vol":        b_vol,
                "b_struct":     b_struct,
                "b_any":        b_any,
                # Birth-anchored pipeline
                "birth_to_sweep": birth_to_sw,
                "s2_sw_to_disp":  s2,
                "s3_disp_to_zone": s3,
                "s4_zone_to_entry": s4,
                # Theoretical timing if entry occurred at birth + PIPELINE_MIN_BARS
                "t_pct":        t_pct,
                "t_bucket":     _bucket(t_pct),
                # Whether birth fires before the current sweep detection
                "birth_before_sweep": 1 if birth_to_sw > 0 else 0,
            })
            last_bar = i
            break

    return trades


# ── Reporting ─────────────────────────────────────────────────────────────────

def _print_birth_anchors(all_trades: List[Dict]) -> None:
    """Table F: How early each birth anchor fires, by current entry bucket."""
    W = 86
    print("\n" + "=" * W)
    print("  TABLE F — Move Birth Anchor Timing  (bars from swing extreme to each event)")
    print("  Capped at bars_since when event not found before entry.")
    print("=" * W)
    hdr = "{:<16}  {:>5}  {:>10}  {:>10}  {:>10}  {:>12}  {:>10}"
    print(hdr.format("Bucket", "N", "B1:Expand", "B2:Volume", "B3:Struct", "B_any(min)", "Current"))
    print("-" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate", "ALL"]
    LABELS  = {"Early": "Early (<25%)", "Mid": "Mid (25-50%)",
                "Late": "Late (50-75%)", "VeryLate": "VeryLate (75%+)", "ALL": "ALL"}

    for bk in BUCKETS:
        bt = all_trades if bk == "ALL" else [t for t in all_trades if t["bucket"] == bk]
        n  = len(bt)
        if n == 0:
            print(hdr.format(LABELS[bk], 0, "-", "-", "-", "-", "-"))
            continue
        avg_exp  = _safe_avg([t["b_expand"] for t in bt])
        avg_vol  = _safe_avg([t["b_vol"]    for t in bt])
        avg_str  = _safe_avg([t["b_struct"] for t in bt])
        avg_any  = _safe_avg([t["b_any"]    for t in bt])
        avg_cur  = _safe_avg([t["bars_since"] for t in bt])
        print(hdr.format(
            LABELS[bk], n,
            f"{avg_exp:.1f}b", f"{avg_vol:.1f}b", f"{avg_str:.1f}b",
            f"{avg_any:.1f}b", f"{avg_cur:.1f}b",
        ))

    # How often birth fires BEFORE the current sweep detection
    before_n = sum(1 for t in all_trades if t["birth_before_sweep"])
    print()
    print(f"  Birth fires before current sweep detection: "
          f"{before_n} / {len(all_trades)} trades ({before_n/len(all_trades)*100:.0f}%)")
    print("=" * W)


def _print_birth_pipeline(all_trades: List[Dict]) -> None:
    """Table G: Full birth-anchored pipeline vs current S1 stage."""
    W = 88
    print("\n" + "=" * W)
    print("  TABLE G — Birth-Anchored Pipeline  (15m bars)")
    print(f"  SwingStart -[B]-> Birth -[B->Swp]-> Sweep -[S2]-> Disp -[S3]-> Zone -[S4]-> Entry")
    print("=" * W)
    hdr = "{:<16}  {:>5}  {:>8}  {:>9}  {:>8}  {:>8}  {:>8}  {:>10}"
    print(hdr.format("Bucket", "N", "B:Swg>Bth", "Bth>Sweep", "S2:Sw>Dp", "S3:Dp>Zn", "S4:Zn>En", "Total"))
    print("-" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate", "ALL"]
    LABELS  = {"Early": "Early (<25%)", "Mid": "Mid (25-50%)",
                "Late": "Late (50-75%)", "VeryLate": "VeryLate (75%+)", "ALL": "ALL"}

    for bk in BUCKETS:
        bt = all_trades if bk == "ALL" else [t for t in all_trades if t["bucket"] == bk]
        n  = len(bt)
        if n == 0:
            print(hdr.format(LABELS[bk], 0, "-", "-", "-", "-", "-", "-"))
            continue
        avg_b   = _safe_avg([t["b_any"]           for t in bt])
        avg_bs  = _safe_avg([t["birth_to_sweep"]   for t in bt])
        avg_s2  = _safe_avg([t["s2_sw_to_disp"]   for t in bt])
        avg_s3  = _safe_avg([t["s3_disp_to_zone"] for t in bt])
        avg_s4  = _safe_avg([t["s4_zone_to_entry"] for t in bt])
        avg_tot = _safe_avg([t["bars_since"]        for t in bt])
        print(hdr.format(
            LABELS[bk], n,
            f"{avg_b:.1f}b", f"{avg_bs:.1f}b", f"{avg_s2:.1f}b",
            f"{avg_s3:.1f}b", f"{avg_s4:.1f}b", f"{avg_tot:.1f}b",
        ))

    # % breakdown of the birth-anchored pipeline for ALL
    bt  = all_trades
    avg_b  = _safe_avg([t["b_any"]           for t in bt])
    avg_bs = _safe_avg([t["birth_to_sweep"]   for t in bt])
    avg_s2 = _safe_avg([t["s2_sw_to_disp"]   for t in bt])
    avg_s3 = _safe_avg([t["s3_disp_to_zone"] for t in bt])
    avg_s4 = _safe_avg([t["s4_zone_to_entry"] for t in bt])
    tot    = avg_b + avg_bs + avg_s2 + avg_s3 + avg_s4
    if tot > 0:
        print()
        print("  ALL — stage share of birth-anchored total:")
        def _p(v: float) -> str:
            return f"{v/tot*100:.0f}% ({v:.1f}b)"
        print(f"    B:Swg->Birth  = {_p(avg_b)}")
        print(f"    Birth->Sweep  = {_p(avg_bs)}")
        print(f"    S2:Swp->Disp  = {_p(avg_s2)}")
        print(f"    S3:Dsp->Zone  = {_p(avg_s3)}")
        print(f"    S4:Zon->Entry = {_p(avg_s4)}")
    print("=" * W)


def _print_birth_shift(all_trades: List[Dict]) -> None:
    """Table H: Actual vs theoretical entry bucket distribution."""
    W = 80
    print("\n" + "=" * W)
    print(f"  TABLE H — Bucket Shift: Actual Entry vs Theoretical Birth+{PIPELINE_MIN_BARS}b Entry")
    print(f"  Theoretical entry = birth bar + {PIPELINE_MIN_BARS} bars (avg S3+S4 from Table E)")
    print("=" * W)

    BUCKETS = ["Early", "Mid", "Late", "VeryLate"]
    n = len(all_trades)

    hdr = "{:<16}  {:>12}  {:>14}"
    print(hdr.format("Bucket", "Actual", "Theoretical"))
    print("-" * W)

    for bk in BUCKETS:
        act = sum(1 for t in all_trades if t["bucket"]   == bk)
        the = sum(1 for t in all_trades if t["t_bucket"] == bk)
        print(hdr.format(
            bk,
            f"{act:>4} ({act/n*100:.1f}%)",
            f"{the:>4} ({the/n*100:.1f}%)",
        ))

    act_lv = sum(1 for t in all_trades if t["bucket"]   in ("Late", "VeryLate"))
    the_lv = sum(1 for t in all_trades if t["t_bucket"] in ("Late", "VeryLate"))
    print("-" * W)
    print(hdr.format(
        "Late+VeryLate",
        f"{act_lv:>4} ({act_lv/n*100:.1f}%)",
        f"{the_lv:>4} ({the_lv/n*100:.1f}%)",
    ))

    # Per-bucket theoretical breakdown
    print()
    print("  Theoretical bucket for each actual bucket (where would trades land?):")
    sub = "{:<16}  {:>10}  {:>10}  {:>10}  {:>10}"
    print("  " + sub.format("Actual bucket", "->Early", "->Mid", "->Late", "->VLate"))
    print("  " + "-" * 50)
    for bk in BUCKETS:
        bt = [t for t in all_trades if t["bucket"] == bk]
        nb = len(bt)
        if nb == 0:
            continue
        def _p(tbk: str) -> str:
            c = sum(1 for t in bt if t["t_bucket"] == tbk)
            return f"{c} ({c/nb*100:.0f}%)"
        print("  " + sub.format(bk, _p("Early"), _p("Mid"), _p("Late"), _p("VeryLate")))

    # Avg theoretical pct_done
    print()
    avg_actual = _safe_avg([t["pct_done"] for t in all_trades])
    avg_theor  = _safe_avg([t["t_pct"]   for t in all_trades])
    avg_saving = avg_actual - avg_theor
    print(f"  Avg pct_done  actual   : {avg_actual:.1f}%")
    print(f"  Avg pct_done  theor    : {avg_theor:.1f}%")
    print(f"  Avg pct saved (earlier): {avg_saving:.1f}%  of swing move")
    print("=" * W)


def _write_birth_csv(all_trades: List[Dict],
                     path: str = "backtest_move_birth.csv") -> None:
    if not all_trades:
        return
    fields = [
        "symbol", "date", "time", "direction", "entry",
        "bars_since", "pct_done", "bucket", "entry_mode",
        "result", "r", "mfe_r", "mae_r",
        "b_expand", "b_vol", "b_struct", "b_any",
        "birth_to_sweep", "s2_sw_to_disp", "s3_disp_to_zone", "s4_zone_to_entry",
        "t_pct", "t_bucket", "birth_before_sweep",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in all_trades:
            w.writerow({k: row.get(k, "") for k in fields})
    print(f"\n  Full birth log saved -> {path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "=" * 80)
    print("  SMART ICT X2 — Move Birth Detector  (prototype diagnostic)")
    print(f"  Symbols : {', '.join(SYMBOLS)}")
    print(f"  Window  : {DAYS} days | Swing lookback: {SWING_LOOKBACK} bars | "
          f"Forward: {SWING_LOOKFWD} bars")
    print(f"  Birth anchors: Expansion(0.8ATR) | Volume(1.3x) | Structure(5-bar break)")
    print(f"  Pipeline min : {PIPELINE_MIN_BARS} bars  (S3+S4 avg from Table E)")
    print(f"  Run at  : {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 80 + "\n")

    all_trades: List[Dict[str, Any]] = []

    for sym in SYMBOLS:
        print(f"  [{sym:5}] scanning ... ", end="", flush=True)
        try:
            trades = _run_birth_symbol(sym)
        except Exception as e:
            print(f"ERROR: {e}")
            continue
        all_trades.extend(trades)
        n  = len(trades)
        b_avg = _safe_avg([t["b_any"] for t in trades]) if trades else 0
        lv_n  = sum(1 for t in trades if t["bucket"] in ("Late", "VeryLate"))
        print(f"{n:>3} signals  "
              f"AvgBirth={b_avg:.1f}b  "
              f"Late+VLate={lv_n} ({lv_n/n*100:.0f}% of {n})" if n else "0 signals")

    if not all_trades:
        print("\nNo trades found.")
        return

    _print_birth_anchors(all_trades)
    _print_birth_pipeline(all_trades)
    _print_birth_shift(all_trades)
    _write_birth_csv(all_trades)

    n   = len(all_trades)
    lv  = sum(1 for t in all_trades if t["bucket"]   in ("Late", "VeryLate"))
    t_lv = sum(1 for t in all_trades if t["t_bucket"] in ("Late", "VeryLate"))
    print(f"\n  SUMMARY: {n} trades | "
          f"Actual Late+VLate = {lv} ({lv/n*100:.1f}%) | "
          f"Theoretical Late+VLate = {t_lv} ({t_lv/n*100:.1f}%)")
    print(f"  Birth fires before current pipeline in "
          f"{sum(1 for t in all_trades if t['birth_before_sweep'])} / {n} trades.")


if __name__ == "__main__":
    main()
