# -*- coding: utf-8 -*-
"""
backtest_runner_x2.py — SMART ICT X2.3 Daily Selective Backtest — BOT 8 SYMBOLS ONLY
متوافق مع analyzer_x2 الحالي الذي يحتوي:
_detect_sweep / _direction_from_htf / _resolve_zone / _trigger_retest

المنطق:
1) يبني MarketState من البيانات التاريخية.
2) يستخدم نفس دوال X2 الحالية قدر الإمكان.
3) يدخل فقط بعد Sweep + Displacement + Zone + Retest.
4) يحاكي TP1=2R / SL / BE بعد انتهاء مدة الاحتفاظ.
"""
from __future__ import annotations

import sys
import warnings
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
import csv
from collections import defaultdict

warnings.filterwarnings("ignore")

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("pip install yfinance pandas")
    sys.exit(1)

try:
    from analyzer_x2 import (
        Candle,
        MarketState,
        Direction,
        SYMBOL_PROFILES,
        _atr,
        _adx_calc,
        _ema,
        _detect_displacement,
        _detect_fvg,
        _detect_order_block,
    )
except Exception as e:
    print(f"Import error from analyzer_x2.py: {e}")
    sys.exit(1)

# أسماء الدوال تغيرت بين نسخ X2، لذلك نربطها بشكل نظيف دون تعديل analyzer_x2.
import analyzer_x2 as _x2

_detect_sweep = getattr(_x2, "_detect_sweep", None) or getattr(_x2, "_detect_liquidity_sweep", None)
_direction_from_htf = getattr(_x2, "_direction_from_htf", None)
_get_1h_trend = getattr(_x2, "_get_1h_trend", None)
_resolve_zone = getattr(_x2, "_resolve_zone", None)
_trigger_retest = getattr(_x2, "_trigger_retest", None)
_volume_ratio = getattr(_x2, "_volume_ratio", None)

if _detect_sweep is None:
    print("ERROR: analyzer_x2.py لا يحتوي _detect_sweep أو _detect_liquidity_sweep")
    sys.exit(1)

# نفس رموز البوت الحالية من trading_app.py / X1_SCAN_SYMBOLS فقط
BOT_SYMBOLS = [
    "QQQ", "NVDA", "MSFT", "META", "AMZN", "NFLX", "GOOGL", "SQQQ",
]
SYMBOLS = list(BOT_SYMBOLS)
DAYS = 55
TP1_R = 1.8
MAX_HOLD = 26       # شموع 15m بعد الدخول
SETUP_COOLDOWN = 16 # شموع بين الصفقات لنفس الرمز
MIN_HISTORY = 160


def _flat(df):
    if df is not None and isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _to_candles(df: pd.DataFrame) -> List[Candle]:
    out: List[Candle] = []
    for ts, row in df.iterrows():
        dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.utcnow()
        out.append(Candle(
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row.get("Volume", 0) or 0),
            timestamp=dt,
        ))
    return out


def _as_dir_name(d: Any) -> str:
    """Direction enum/string → LONG/SHORT/NEUTRAL."""
    v = getattr(d, "value", d)
    s = str(v).upper()
    if s in ("LONG", "BULL", "CALL", "BUY"):
        return "LONG"
    if s in ("SHORT", "BEAR", "PUT", "SELL"):
        return "SHORT"
    return "NEUTRAL"


def _dir_enum(name: str):
    if name == "LONG":
        return Direction.LONG
    if name == "SHORT":
        return Direction.SHORT
    return Direction.NEUTRAL


def _market_state_from(candles: List[Candle], price: float) -> MarketState:
    closes = [c.close for c in candles]
    vols = [c.volume for c in candles[-21:]]
    atr = _atr(candles, 14) if len(candles) >= 20 else max(price * 0.01, 0.01)
    adx = _adx_calc(candles, 14) if len(candles) >= 40 else 20.0
    ema50 = _ema(closes, 50) if len(closes) >= 5 else price
    ema200 = _ema(closes, 200) if len(closes) >= 20 else _ema(closes, len(closes))
    avg_vol = sum(vols[:-1]) / max(1, len(vols[:-1])) if len(vols) > 1 else (vols[-1] if vols else 1.0)
    return MarketState(
        vix=18.0,
        adx=float(adx or 0),
        volume=float(vols[-1] if vols else 0),
        avg_volume_20=float(avg_vol or 1),
        ema_50=float(ema50 or price),
        ema_200=float(ema200 or price),
        price=float(price),
        atr_14=float(atr or max(price * 0.01, 0.01)),
        news_risk="LOW",
    )


def _htf_direction(c1h: List[Candle], market: MarketState) -> str:
    if _direction_from_htf:
        try:
            out = _direction_from_htf(c1h, market)
            # X2.1 returns (Direction, score, detail)
            if isinstance(out, tuple):
                return _as_dir_name(out[0])
            return _as_dir_name(out)
        except TypeError:
            pass
        except Exception:
            return "NEUTRAL"
    if _get_1h_trend:
        try:
            out = _get_1h_trend(c1h)
            return _as_dir_name(out)
        except Exception:
            return "NEUTRAL"
    return "NEUTRAL"


def _zone_bounds(zone: Any) -> Optional[Tuple[float, float]]:
    if zone is None:
        return None
    if isinstance(zone, tuple) and len(zone) >= 2:
        return max(float(zone[0]), float(zone[1])), min(float(zone[0]), float(zone[1]))
    top = getattr(zone, "top", None)
    bottom = getattr(zone, "bottom", None)
    if top is not None and bottom is not None:
        return max(float(top), float(bottom)), min(float(top), float(bottom))
    return None


def _zone_mid(zone: Any) -> float:
    mid = getattr(zone, "mid", None)
    if mid is not None:
        return float(mid)
    b = _zone_bounds(zone)
    return (b[0] + b[1]) / 2 if b else 0.0


def _detect_zone(candles: List[Candle], direction_name: str, atr: float, after_index: int = 0) -> Optional[Any]:
    direction = _dir_enum(direction_name)
    # أفضل توافق مع X2.1 Elite: _resolve_zone يرجع zone + debug
    if _resolve_zone:
        try:
            z, _debug = _resolve_zone(candles, direction, atr, after_index)
            if z is not None:
                return z
        except Exception:
            pass
    # fallback للأسماء القديمة/البسيطة
    for fn in (_detect_fvg, _detect_order_block):
        try:
            try:
                z = fn(candles, direction, atr, after_index)
            except TypeError:
                z = fn(candles, direction_name, atr)
            if z is not None:
                return z
        except Exception:
            continue
    return None


def _manual_retest(candle: Candle, zone: Any, direction_name: str, atr: float) -> bool:
    b = _zone_bounds(zone)
    if not b:
        return False
    z_top, z_bot = b
    mid = _zone_mid(zone)
    touched = candle.low <= z_top and candle.high >= z_bot
    dist_atr = abs(candle.close - mid) / max(atr, 0.01)
    if not touched and dist_atr > 0.65:
        return False
    body = abs(candle.close - candle.open)
    body_atr = body / max(atr, 0.01)
    if body_atr < 0.18:
        return False
    if direction_name == "LONG":
        return candle.close > mid and candle.close > candle.open
    if direction_name == "SHORT":
        return candle.close < mid and candle.close < candle.open
    return False


def _is_retest(trigger_candles: List[Candle], zone: Any, direction_name: str, atr: float) -> bool:
    if not trigger_candles:
        return False
    direction = _dir_enum(direction_name)
    if _trigger_retest:
        try:
            rt, _debug = _trigger_retest(trigger_candles, zone, direction, atr)
            return rt is not None
        except Exception:
            pass
    return _manual_retest(trigger_candles[-1], zone, direction_name, atr)


def _call_detect_sweep(candles: List[Candle], direction_name: str, atr: float) -> Any:
    try:
        return _detect_sweep(candles, _dir_enum(direction_name), atr)
    except Exception:
        try:
            return _detect_sweep(candles, direction_name, atr)
        except Exception:
            return None


def _call_detect_displacement(candles: List[Candle], direction_name: str, atr: float, after_index: int) -> Any:
    try:
        return _detect_displacement(candles, _dir_enum(direction_name), atr, after_index)
    except TypeError:
        try:
            return _detect_displacement(candles, direction_name, atr)
        except Exception:
            return None
    except Exception:
        return None


def _event_index(event: Any) -> int:
    return int(getattr(event, "index", 0) or 0)


def backtest_symbol(symbol: str) -> Dict[str, Any]:
    end = datetime.today()
    start = end - timedelta(days=DAYS)

    try:
        df15 = _flat(yf.download(symbol, start=start, end=end, interval="15m", progress=False, auto_adjust=True))
        df1h = _flat(yf.download(symbol, start=start, end=end, interval="1h", progress=False, auto_adjust=True))
    except Exception as e:
        return {"symbol": symbol, "error": str(e), "trades": 0}

    if df15 is None or len(df15) < MIN_HISTORY:
        return {"symbol": symbol, "error": "data short", "trades": 0}

    c15_all = _to_candles(df15)
    c1h_all = _to_candles(df1h) if df1h is not None and len(df1h) > 20 else []

    # لا نستخدم __DEFAULT__ المعطّل لرموز البوت؛ SQQQ قد لا يكون في profiles.
    default_profile = {"enabled": True, "timeframe": "15m" if symbol in {"NVDA", "SQQQ"} else "1H",
                       "min_conf": 72, "min_adx": 17, "max_adx": 68}
    profile = dict(default_profile)
    if isinstance(SYMBOL_PROFILES.get(symbol), dict):
        profile.update(SYMBOL_PROFILES.get(symbol))
    tf = profile.get("timeframe", "1H")
    min_adx = float(profile.get("min_adx", 18))
    max_adx = float(profile.get("max_adx", 999))
    min_conf = float(profile.get("min_conf", profile.get("min_score", 70)))

    res = {"symbol": symbol, "trades": 0, "wins": 0, "losses": 0, "be": 0, "trade_log": []}
    last_trade_bar = -999

    for i in range(MIN_HISTORY, len(c15_all) - MAX_HOLD - 1):
        c15 = c15_all[:i + 1]
        ts = c15_all[i].timestamp
        c1h = [c for c in c1h_all if c.timestamp <= ts] or c15

        setup_candles = c15[-220:] if tf == "15m" else (c1h[-300:] if c1h and len(c1h) >= 60 else c15[-220:])
        trigger_candles = c15[-8:]
        if len(setup_candles) < 60:
            continue

        price = c15_all[i].close
        market = _market_state_from(setup_candles, price)
        atr = market.atr_14
        adx = market.adx
        if adx < min_adx or adx > max_adx:
            continue
        if (i - last_trade_bar) < SETUP_COOLDOWN:
            continue

        htf = _htf_direction(c1h[-300:], market)
        directions = ["LONG", "SHORT"] if htf == "NEUTRAL" else [htf]

        for direction in directions:
            # X2.3 Daily Selective:
            # لا نشترط Sweep AND Displacement دائماً؛ هذا سبب صفر/صفقة واحدة.
            # نطلب على الأقل حدثاً مؤسسياً واحداً: Sweep أو Displacement، ثم Zone + Retest.
            sweep = _call_detect_sweep(setup_candles, direction, atr)
            after_idx = _event_index(sweep) if sweep is not None else max(0, len(setup_candles) - 36)
            displacement = _call_detect_displacement(setup_candles, direction, atr, after_idx)
            if sweep is None and displacement is None:
                continue
            zone_after = _event_index(displacement) if displacement is not None else after_idx
            zone = _detect_zone(setup_candles, direction, atr, zone_after)
            if zone is None:
                continue
            zb = _zone_bounds(zone)
            if not zb:
                continue
            z_top, z_bot = zb
            z_mid = _zone_mid(zone)

            # السعر لا يكون بعيداً جداً عن المنطقة عند التفعيل.
            if abs(price - z_mid) / max(atr, 0.01) > 3.2:
                continue

            # Retest / reclaim من آخر شموع 15m.
            if not _is_retest(trigger_candles, zone, direction, atr):
                continue

            # Score قريب من منطق X2 Elite.
            sweep_q = float(getattr(sweep, "quality", 0.0) or 0.0) if sweep is not None else 0.0
            disp_q = float(getattr(displacement, "quality", 0.0) or 0.0) if displacement is not None else 0.0
            zone_q = float(getattr(zone, "quality", 0.7) or 0.7)
            fresh = float(getattr(zone, "freshness", 0.7) or 0.7)
            vr = _volume_ratio(setup_candles) if _volume_ratio else 1.0
            score = 30 + sweep_q * 14 + disp_q * 14 + zone_q * 20 + fresh * 10
            if sweep is not None and displacement is not None:
                score += 6
            score += 5 if htf != "NEUTRAL" else 0
            score += 5 if vr >= 0.8 else 2
            if score < min_conf:
                continue

            entry = c15_all[i].close
            stop = z_bot - atr * 0.22 if direction == "LONG" else z_top + atr * 0.22
            risk = abs(entry - stop)
            if risk <= 0:
                continue
            tp1 = entry + risk * TP1_R if direction == "LONG" else entry - risk * TP1_R

            outcome = "BE"
            exit_price = entry
            exit_bar_index = i
            hold_bars = 0
            r_mult = 0.0
            mfe_r = 0.0
            mae_r = 0.0
            for j, fc in enumerate(c15_all[i + 1:i + 1 + MAX_HOLD + 1], start=1):
                # MFE / MAE on underlying price in R units
                if direction == "LONG":
                    mfe_r = max(mfe_r, (fc.high - entry) / risk)
                    mae_r = min(mae_r, (fc.low - entry) / risk)
                    if fc.low <= stop:
                        outcome = "LOSS"; exit_price = stop; exit_bar_index = i + j; hold_bars = j; r_mult = -1.0; break
                    if fc.high >= tp1:
                        outcome = "WIN"; exit_price = tp1; exit_bar_index = i + j; hold_bars = j; r_mult = TP1_R; break
                else:
                    mfe_r = max(mfe_r, (entry - fc.low) / risk)
                    mae_r = min(mae_r, (entry - fc.high) / risk)
                    if fc.high >= stop:
                        outcome = "LOSS"; exit_price = stop; exit_bar_index = i + j; hold_bars = j; r_mult = -1.0; break
                    if fc.low <= tp1:
                        outcome = "WIN"; exit_price = tp1; exit_bar_index = i + j; hold_bars = j; r_mult = TP1_R; break
            else:
                hold_bars = MAX_HOLD
                exit_bar_index = min(i + MAX_HOLD, len(c15_all)-1)
                exit_price = c15_all[exit_bar_index].close
                r_mult = 0.0

            res["trades"] += 1
            if outcome == "WIN":
                res["wins"] += 1
            elif outcome == "LOSS":
                res["losses"] += 1
            else:
                res["be"] += 1

            res["trade_log"].append({
                "date": c15_all[i].timestamp.strftime("%Y-%m-%d"),
                "time": c15_all[i].timestamp.strftime("%H:%M"),
                "symbol": symbol,
                "direction": "CALL" if direction == "LONG" else "PUT",
                "entry": round(entry, 4),
                "stop": round(stop, 4),
                "tp1": round(tp1, 4),
                "exit_price": round(exit_price, 4),
                "result": outcome,
                "r": round(r_mult, 2),
                "score": round(score, 1),
                "adx": round(adx, 1),
                "atr": round(atr, 4),
                "hold_bars": hold_bars,
                "hold_minutes": hold_bars * 15,
                "mfe_r": round(mfe_r, 2),
                "mae_r": round(mae_r, 2),
            })
            last_trade_bar = i
            break

    decisive = res["wins"] + res["losses"]
    res["win_rate"] = round(res["wins"] / decisive * 100, 1) if decisive else 0.0
    res["trades_per_day"] = round(res["trades"] / DAYS, 2)
    total_r = sum(t["r"] for t in res["trade_log"])
    gross_win_r = sum(t["r"] for t in res["trade_log"] if t["r"] > 0)
    gross_loss_r = abs(sum(t["r"] for t in res["trade_log"] if t["r"] < 0))
    res["total_r"] = round(total_r, 2)
    res["avg_r"] = round(total_r / res["trades"], 2) if res["trades"] else 0.0
    res["profit_factor"] = round(gross_win_r / gross_loss_r, 2) if gross_loss_r > 0 else (99.0 if gross_win_r > 0 else 0.0)
    res["avg_entry"] = round(sum(t["entry"] for t in res["trade_log"]) / res["trades"], 2) if res["trades"] else 0.0
    res["avg_hold_min"] = round(sum(t["hold_minutes"] for t in res["trade_log"]) / res["trades"], 1) if res["trades"] else 0.0
    return res


def _max_streak(trades: List[Dict[str, Any]], target: str) -> int:
    best = cur = 0
    for t in trades:
        if t.get("result") == target:
            cur += 1
            best = max(best, cur)
        elif t.get("result") in ("WIN", "LOSS"):
            cur = 0
    return best


def _write_trade_csv(trades: List[Dict[str, Any]], path: str = "backtest_trades_x2.csv") -> None:
    if not trades:
        return
    fields = [
        "date", "time", "symbol", "direction", "entry", "stop", "tp1",
        "exit_price", "result", "r", "score", "adx", "atr",
        "hold_bars", "hold_minutes", "mfe_r", "mae_r",
    ]
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in trades:
            w.writerow({k: row.get(k, "") for k in fields})


def main() -> None:
    print("\n" + "=" * 100)
    print(f"  SMART ICT X2 Backtest — {DAYS} days | {len(SYMBOLS)} bot symbols فقط")
    print(f"  Path: (Sweep OR Displacement) + Zone + Retest | TP1={TP1_R}R | MAX_HOLD={MAX_HOLD} bars")
    print("=" * 100 + "\n")

    all_r: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []
    tw = tl = tb = tt = 0
    total_r = 0.0

    print("{:<7} {:>7} {:>5} {:>5} {:>5} {:>8} {:>8} {:>8} {:>9} {:>9} {:>9}".format(
        "Symbol", "Trades", "W", "L", "BE", "WR%", "/Day", "AvgEntry", "PF", "TotalR", "AvgHold"
    ))
    print("-" * 100)

    for sym in SYMBOLS:
        r = backtest_symbol(sym)
        all_r.append(r)
        if "error" in r:
            print(f"{sym:<7} ERROR: {r['error']}")
            continue
        all_trades.extend(r.get("trade_log", []))
        t, w, l, b, wr = r["trades"], r["wins"], r["losses"], r["be"], r["win_rate"]
        tt += t; tw += w; tl += l; tb += b; total_r += r.get("total_r", 0.0)
        print("{:<7} {:>7} {:>5} {:>5} {:>5} {:>7.1f}% {:>8.2f} {:>8.2f} {:>9.2f} {:>9.2f} {:>8.0f}m".format(
            sym, t, w, l, b, wr, r["trades_per_day"], r.get("avg_entry", 0.0),
            r.get("profit_factor", 0.0), r.get("total_r", 0.0), r.get("avg_hold_min", 0.0)
        ))

    print("-" * 100)
    dec = tw + tl
    ov = round(tw / dec * 100, 1) if dec else 0.0
    tpd = round(tt / DAYS, 2)
    gross_win_r = sum(t["r"] for t in all_trades if t["r"] > 0)
    gross_loss_r = abs(sum(t["r"] for t in all_trades if t["r"] < 0))
    pf = round(gross_win_r / gross_loss_r, 2) if gross_loss_r > 0 else (99.0 if gross_win_r > 0 else 0.0)
    avg_r = round(total_r / tt, 2) if tt else 0.0
    avg_entry = round(sum(t["entry"] for t in all_trades) / tt, 2) if tt else 0.0
    avg_hold = round(sum(t["hold_minutes"] for t in all_trades) / tt, 1) if tt else 0.0

    print(f"TOTAL: {tt} trades | {tw} رابحة | {tl} خاسرة | {tb} تعادل | WR={ov:.1f}%")
    print(f"Per day: {tpd:.2f} صفقة/يوم | Avg entry price=${avg_entry:.2f} | PF={pf:.2f} | TotalR={total_r:.2f}R | AvgR={avg_r:.2f}R | AvgHold={avg_hold:.0f}m")
    print(f"Max win streak: {_max_streak(all_trades, 'WIN')} | Max loss streak: {_max_streak(all_trades, 'LOSS')}")

    # Daily distribution
    by_day: Dict[str, int] = defaultdict(int)
    for tr in all_trades:
        by_day[tr["date"]] += 1
    active_days = len(by_day)
    avg_active_day = round(tt / active_days, 2) if active_days else 0.0
    max_day = max(by_day.values()) if by_day else 0
    print(f"Active trading days: {active_days} | Avg on active day: {avg_active_day:.2f} | Max trades in one day: {max_day}")

    # Direction stats
    calls = [t for t in all_trades if t["direction"] == "CALL"]
    puts = [t for t in all_trades if t["direction"] == "PUT"]
    def _wr(rows):
        w = sum(1 for x in rows if x["result"] == "WIN")
        l = sum(1 for x in rows if x["result"] == "LOSS")
        return round(w / (w + l) * 100, 1) if (w + l) else 0.0
    print(f"CALL: {len(calls)} trades | WR={_wr(calls):.1f}%   | PUT: {len(puts)} trades | WR={_wr(puts):.1f}%")

    _write_trade_csv(all_trades)
    print("\nSaved trade log: backtest_trades_x2.csv")
    print("Columns: date, time, symbol, CALL/PUT, entry, stop, tp1, exit_price, result, R, score, ADX, hold_minutes")
    print()


if __name__ == "__main__":
    main()
