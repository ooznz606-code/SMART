
import json, csv, math
from pathlib import Path
from datetime import datetime, timedelta, time

DATA_DIR = Path("chart_data")
OUT_DIR = Path("reports")
OUT_DIR.mkdir(exist_ok=True)

BASELINE = {"N":14, "WR":61.5, "PF":2.88, "TotalR":9.40, "MaxDD":3.20}

def parse_dt(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return datetime.fromtimestamp(x/1000 if x > 10_000_000_000 else x)
    s = str(x).replace("T"," ").replace("Z","").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S","%Y-%m-%d %H:%M","%Y-%m-%d"):
        try:
            return datetime.strptime(s[:19], fmt)
        except:
            pass
    return None

def infer_tf_minutes(tf):
    tf = str(tf).lower().replace(" ","")
    if tf in ("1m","1min"): return 1
    if tf in ("5m","5min"): return 5
    if tf in ("15m","15min","15mm"): return 15
    if tf in ("1h","60m"): return 60
    if tf in ("4h","240m"): return 240
    if tf in ("1d","d","day"): return 1440
    return 5

def file_symbol_tf(path):
    stem = path.stem
    parts = stem.split("_")
    if len(parts) >= 2:
        return "_".join(parts[:-1]).upper(), parts[-1]
    return stem.upper(), "5m"

def load_file(path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except:
        return []

    symbol, tf_from_name = file_symbol_tf(path)

    if isinstance(data, list):
        rows = []
        for r in data:
            if not isinstance(r, dict): 
                continue
            dt = parse_dt(r.get("timestamp") or r.get("time") or r.get("datetime") or r.get("date"))
            if not dt:
                continue
            try:
                rows.append({
                    "dt": dt,
                    "open": float(r["open"]),
                    "high": float(r["high"]),
                    "low": float(r["low"]),
                    "close": float(r["close"]),
                    "volume": float(r.get("volume", 0) or 0),
                })
            except:
                continue
        return sorted(rows, key=lambda x:x["dt"])

    if not isinstance(data, dict):
        return []

    opens = data.get("opens") or data.get("open") or []
    highs = data.get("highs") or data.get("high") or []
    lows = data.get("lows") or data.get("low") or []
    closes = data.get("closes") or data.get("close") or []
    vols = data.get("volumes") or data.get("volume") or data.get("vols") or []
    times = data.get("timestamps") or data.get("times") or data.get("time") or data.get("datetime") or data.get("dates") or []

    n = min(len(opens), len(highs), len(lows), len(closes))
    if n < 100:
        return []

    tf = data.get("tf") or data.get("timeframe") or tf_from_name
    step = infer_tf_minutes(tf)

    parsed_times = [parse_dt(x) for x in times[:n]] if isinstance(times, list) else []
    has_times = len(parsed_times) >= n and all(x is not None for x in parsed_times[:n])

    if not has_times:
        saved_at = parse_dt(data.get("saved_at"))
        if saved_at:
            start = saved_at - timedelta(minutes=step*(n-1))
        else:
            start = datetime(2024,1,1,9,30)
        parsed_times = [start + timedelta(minutes=step*i) for i in range(n)]

    rows = []
    for i in range(n):
        try:
            rows.append({
                "dt": parsed_times[i],
                "open": float(opens[i]),
                "high": float(highs[i]),
                "low": float(lows[i]),
                "close": float(closes[i]),
                "volume": float(vols[i]) if isinstance(vols, list) and i < len(vols) else 1.0,
            })
        except:
            continue

    return sorted(rows, key=lambda x:x["dt"])

def ema(vals, n):
    out, cur = [], None
    k = 2/(n+1)
    for v in vals:
        cur = v if cur is None else v*k + cur*(1-k)
        out.append(cur)
    return out

def atr(rows, n=14):
    out, trs, prev = [], [], None
    for r in rows:
        tr = r["high"]-r["low"] if prev is None else max(r["high"]-r["low"], abs(r["high"]-prev), abs(r["low"]-prev))
        trs.append(tr)
        out.append(sum(trs[-n:])/min(len(trs),n))
        prev = r["close"]
    return out

def rvol(rows, n=20):
    out, vols = [], []
    for r in rows:
        vols.append(r["volume"])
        avg = sum(vols[-n:]) / max(1, min(len(vols), n))
        out.append(r["volume"]/avg if avg > 0 else 1)
    return out

def adx_proxy(rows, atrs):
    out = []
    for i in range(len(rows)):
        if i < 10:
            out.append(0)
        else:
            move = abs(rows[i]["close"] - rows[i-10]["close"])
            base = atrs[i] * 10
            out.append(min(60, 100*move/base) if base > 0 else 0)
    return out

def score(trades):
    if not trades:
        return None
    w = sum(1 for x in trades if x["R"] > 0)
    l = sum(1 for x in trades if x["R"] < 0)
    be = sum(1 for x in trades if x["R"] == 0)
    gw = sum(x["R"] for x in trades if x["R"] > 0)
    gl = abs(sum(x["R"] for x in trades if x["R"] < 0))
    pf = gw/gl if gl else 999
    eq = peak = dd = 0
    for x in trades:
        eq += x["R"]
        peak = max(peak, eq)
        dd = max(dd, peak-eq)
    days = sorted(set(x["day"] for x in trades))
    return {
        "N":len(trades), "W":w, "L":l, "BE":be,
        "WR":w/max(1,w+l)*100, "PF":pf,
        "TotalR":sum(x["R"] for x in trades),
        "MaxDD":dd,
        "TradesPerDay":len(trades)/max(1,len(days))
    }

def run_orb(symbol, rows, cfg):
    closes = [r["close"] for r in rows]
    e20 = ema(closes,20)
    a = atr(rows,14)
    rv = rvol(rows,20)
    ax = adx_proxy(rows,a)

    by_day = {}
    for i,r in enumerate(rows):
        by_day.setdefault(r["dt"].date(), []).append((i,r))

    trades = []
    st = time.fromisoformat(cfg["entry_start"])
    et = time.fromisoformat(cfg["entry_end"])

    for day, items in by_day.items():
        day_trades = 0
        stopped = False

        orb = [(i,r) for i,r in items if time(9,30) <= r["dt"].time() <= time(9,45)]
        if len(orb) < 2:
            continue

        oh = max(r["high"] for _,r in orb)
        ol = min(r["low"] for _,r in orb)
        mid = (oh+ol)/2
        orb_pct = (oh-ol)/mid if mid else 0

        if orb_pct > cfg["max_orb_range"]:
            continue

        for i,r in items:
            if stopped or day_trades >= cfg["max_trades_per_day"]:
                break
            if not (st <= r["dt"].time() <= et):
                continue
            if i < 25:
                continue
            if ax[i] < cfg["min_adx"]:
                continue
            if rv[i] < cfg["min_rvol"]:
                continue
            if abs(r["close"]-e20[i])/r["close"] > cfg["max_ema_dist"]:
                continue

            direction = None
            if r["close"] > oh and r["close"] > e20[i]:
                direction = "LONG"
            elif r["close"] < ol and r["close"] < e20[i]:
                direction = "SHORT"
            else:
                continue

            entry = r["close"]
            risk = max(a[i]*0.8, entry*0.002)
            stop = entry-risk if direction=="LONG" else entry+risk
            tp = entry+risk*1.8 if direction=="LONG" else entry-risk*1.8

            R = None
            for j in range(i+1, min(i+18, len(rows))):
                x = rows[j]
                if direction == "LONG":
                    if x["low"] <= stop:
                        R = -1; break
                    if x["high"] >= tp:
                        R = 1.8; break
                else:
                    if x["high"] >= stop:
                        R = -1; break
                    if x["low"] <= tp:
                        R = 1.8; break
            if R is None:
                R = 0

            trades.append({"symbol":symbol, "day":str(day), "time":r["dt"].strftime("%H:%M"), "R":R})
            day_trades += 1
            if cfg["stop_after_first_loss"] and R < 0:
                stopped = True

    return trades

def write_csv(path, rows):
    if not rows:
        path.write_text("", encoding="utf-8-sig")
        return
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

def main():
    files = list(DATA_DIR.glob("*_5m.json"))
    data = {}

    for f in files:
        sym, tf = file_symbol_tf(f)
        if sym == "TEST":
            continue
        rows = load_file(f)
        if len(rows) >= 100:
            data[sym] = rows

    print(f"Loaded symbols: {len(data)}")
    print("Symbols:", ", ".join(sorted(data.keys())[:80]))

    if not data:
        print("No readable 5m data.")
        return

    configs = []
    for entry_start, entry_end in [("09:35","10:30"),("09:35","11:00"),("09:45","11:30"),("10:00","12:00"),("09:35","14:30")]:
        for min_adx in [10,14,18]:
            for min_rvol in [1.0,1.2]:
                for max_orb_range in [0.005,0.010,0.015]:
                    for max_ema_dist in [0.004,0.008,0.012]:
                        for max_trades_per_day in [1,2]:
                            for stop_after_first_loss in [False, True]:
                                configs.append({
                                    "entry_start":entry_start,
                                    "entry_end":entry_end,
                                    "min_adx":min_adx,
                                    "min_rvol":min_rvol,
                                    "max_orb_range":max_orb_range,
                                    "max_ema_dist":max_ema_dist,
                                    "max_trades_per_day":max_trades_per_day,
                                    "stop_after_first_loss":stop_after_first_loss,
                                })

    results = []
    symbol_rows = []
    removal_rows = []

    for cfg in configs:
        all_trades = []
        per_sym = {}
        for sym, rows in data.items():
            tr = run_orb(sym, rows, cfg)
            all_trades += tr
            sc = score(tr)
            if sc:
                per_sym[sym] = sc

        sc_all = score(all_trades)
        if not sc_all:
            continue

        row = {**cfg, **sc_all}
        row["PASS"] = row["PF"] >= 2.5 and row["MaxDD"] <= 3.0 and row["N"] >= BASELINE["N"]
        results.append(row)

    results.sort(key=lambda x:(x["PASS"], x["PF"], x["TotalR"], -x["MaxDD"], x["N"]), reverse=True)

    best = results[0] if results else None

    if best:
        cfg = {k:best[k] for k in ["entry_start","entry_end","min_adx","min_rvol","max_orb_range","max_ema_dist","max_trades_per_day","stop_after_first_loss"]}

        for sym, rows in data.items():
            sc = score(run_orb(sym, rows, cfg))
            if sc:
                symbol_rows.append({"symbol":sym, **sc})

        symbol_rows.sort(key=lambda x:(x["PF"], x["TotalR"], x["N"]), reverse=True)

        base_trades = []
        for sym, rows in data.items():
            base_trades += run_orb(sym, rows, cfg)
        base_sc = score(base_trades)

        for remove_sym in sorted(data):
            tr = []
            for sym, rows in data.items():
                if sym != remove_sym:
                    tr += run_orb(sym, rows, cfg)
            sc = score(tr)
            if sc:
                removal_rows.append({
                    "removed_symbol": remove_sym,
                    **sc,
                    "PF_change": sc["PF"] - base_sc["PF"],
                    "DD_change": sc["MaxDD"] - base_sc["MaxDD"],
                    "TotalR_change": sc["TotalR"] - base_sc["TotalR"],
                })

        removal_rows.sort(key=lambda x:(x["PF_change"], x["TotalR_change"], -x["DD_change"]), reverse=True)

    write_csv(OUT_DIR/"orb_expansion_results.csv", results[:1000])
    write_csv(OUT_DIR/"orb_symbol_ranking.csv", symbol_rows)
    write_csv(OUT_DIR/"orb_symbol_removal_impact.csv", removal_rows)

    print("\n================================================================")
    print("ORB EXPANSION RESEARCH - SMART FORMAT")
    print("================================================================")
    print(f"Baseline: N={BASELINE['N']} WR={BASELINE['WR']} PF={BASELINE['PF']} TotalR={BASELINE['TotalR']} MaxDD={BASELINE['MaxDD']}")
    print("Saved:")
    print(" - reports\\orb_expansion_results.csv")
    print(" - reports\\orb_symbol_ranking.csv")
    print(" - reports\\orb_symbol_removal_impact.csv")

    print("\nTOP 20 CONFIGS:")
    for r in results[:20]:
        print(
            f"PASS={r['PASS']} | N={r['N']:4d} WR={r['WR']:5.1f}% PF={r['PF']:6.2f} "
            f"TotalR={r['TotalR']:7.2f} MaxDD={r['MaxDD']:5.2f} TPD={r['TradesPerDay']:.2f} | "
            f"{r['entry_start']}-{r['entry_end']} ADX>={r['min_adx']} RVOL>={r['min_rvol']} "
            f"ORB<={r['max_orb_range']} EMA<={r['max_ema_dist']} "
            f"max/day={r['max_trades_per_day']} stopLossDay={r['stop_after_first_loss']}"
        )

    print("\nTOP SYMBOLS USING BEST CONFIG:")
    for s in symbol_rows[:25]:
        print(f"{s['symbol']:7s} N={s['N']:3d} WR={s['WR']:5.1f}% PF={s['PF']:6.2f} TotalR={s['TotalR']:7.2f} MaxDD={s['MaxDD']:5.2f}")

    print("\nBEST REMOVALS:")
    for r in removal_rows[:15]:
        print(f"Remove {r['removed_symbol']:7s} | PF={r['PF']:6.2f} ?PF={r['PF_change']:+.2f} TotalR={r['TotalR']:7.2f} MaxDD={r['MaxDD']:5.2f}")

if __name__ == "__main__":
    main()
