"""Confluence Board study, exactly as pre-registered in
docs/research/2026-11-14-oracle-confluence-board.md.

Usage: python3 board_confluence.py <signals.csv> <bybit_daily_cache_dir>
signals.csv: symbol,ts,score,bull,bear,pnl_4h_pct,pnl_24h_pct (export query in the note's result).
Daily cache: <dir>/<SYMBOL>.json = {day_ms: [open, close, turnover]} (written by xs_trend.py).
"""
import csv, json, os, statistics as st, sys

DAY, COOLDOWN, COST, Z, TOP = 86_400, 120 * 60, 0.15, 1.28, 50
rows = list(csv.DictReader(open(sys.argv[1])))
turn = {}  # day_start -> {symbol: turnover}
for f in os.listdir(sys.argv[2]):
    for t, v in json.load(open(os.path.join(sys.argv[2], f))).items():
        turn.setdefault(int(t) // 1000, {})[f[:-5]] = v[2]
top = {d: set(sorted(m, key=m.get, reverse=True)[:TOP]) for d, m in turn.items()}


def entries(universe_filter):
    last, out = {}, []
    for r in rows:
        bull, bear = int(r["bull"]), int(r["bear"])
        side = 1 if bull >= 3 and bull > bear else -1 if bear >= 3 and bear > bull else 0
        if not side:
            continue
        s, ts = r["symbol"], int(r["ts"])
        day = ts // DAY * DAY
        if universe_filter and s not in top.get(day - DAY, ()):
            continue
        if ts - last.get(s, -10**12) < COOLDOWN:
            continue
        last[s] = ts
        p24 = r["pnl_24h_pct"]
        out.append({"day": day, "side": side, "score": int(r["score"]),
                    "r4": side * float(r["pnl_4h_pct"]), "r24": side * float(p24) if p24 else None})
    return out


def daily(es, key):
    by = {}
    for e in es:
        if e[key] is not None:
            by.setdefault(e["day"], []).append(e[key])
    return [st.fmean(by[d]) - COST for d in sorted(by)]


def line(name, es, key="r4"):
    d = daily(es, key)
    if len(d) < 2:
        return print(f"{name}: n={len(es)} days={len(d)}")
    m, se = st.fmean(d), st.stdev(d) / len(d) ** 0.5
    raw = st.fmean(e[key] for e in es if e[key] is not None)
    print(f"{name}: entries {len(es)} days {len(d)}  net/day {m:+.3f}% (before cost {m + COST:+.3f}%)  "
          f"sd {st.stdev(d):.3f}  bounds {m - Z * se:+.3f}..{m + Z * se:+.3f}  per-entry avg {raw:+.3f}%  "
          f"days>0 {sum(x > 0 for x in d)}/{len(d)}")
    return d, m, se


es = entries(True)
print("== PRIMARY: top-50 universe, 4h ==")
d, m, se = line("board rule", es)
h1, h2 = d[:len(d) // 2], d[len(d) // 2:]
print(f"   halves {st.fmean(h1):+.3f}% ({len(h1)}d)  {st.fmean(h2):+.3f}% ({len(h2)}d)   80%-power edge {(Z + 0.84) * se + COST:+.3f}% before cost")
if len(d) >= 30 and len(es) >= 100 and m > Z * se and st.fmean(h1) > 0 and st.fmean(h2) > 0:
    print("   VERDICT: PASS")
elif m + Z * se < 0:
    print("   VERDICT: RULED OUT")
else:
    print("   VERDICT: INCONCLUSIVE")
print("== context ==")
line("24h", es, "r24")
line("longs", [e for e in es if e["side"] > 0])
line("shorts", [e for e in es if e["side"] < 0])
line("|score|>=40", [e for e in es if abs(e["score"]) >= 40])
line("every symbol", entries(False))
