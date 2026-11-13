"""Weekly cross-sectional trend study, exactly as pre-registered in
docs/research/2026-11-13-weekly-cross-sectional-trend.md.

Usage: python3 xs_trend.py <cache_dir>   (public Bybit endpoints only; data is cached there)
"""
import json, os, statistics as st, sys, time, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

CACHE = sys.argv[1]
UTC = timezone.utc
DAY = 86_400_000
WARM = datetime(2022, 1, 1, tzinfo=UTC)
FIRST = datetime(2022, 6, 6, tzinfo=UTC)
TODAY = datetime(2026, 11, 13, tzinfo=UTC)
STABLE = {"USDC", "BUSD", "DAI", "TUSD", "FDUSD", "USDE", "PYUSD"}
N_UNI, N_LEG, COST, Z = 30, 10, 0.0010, 1.28
os.makedirs(f"{CACHE}/d", exist_ok=True)
os.makedirs(f"{CACHE}/f", exist_ok=True)


def ms(dt):
    return int(dt.timestamp() * 1000)


def get(path, params):
    url = f"https://api.bybit.com{path}?" + urllib.parse.urlencode(params)
    for a in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kline-research/1.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=20).read())
            if d.get("retCode") == 0:
                return d["result"]
            err = d.get("retMsg")
        except Exception as e:
            err = e
        time.sleep(1.5 * (a + 1))
    raise SystemExit(f"fetch failed {path} {params}: {err}")


def instruments():
    p = f"{CACHE}/instruments.json"
    if os.path.exists(p):
        return json.load(open(p))
    out = {}
    for status in ("Trading", "Closed"):
        cur = ""
        while True:
            q = {"category": "linear", "limit": 1000, "status": status}
            if cur:
                q["cursor"] = cur
            r = get("/v5/market/instruments-info", q)
            for i in r["list"]:
                if i.get("quoteCoin") == "USDT" and i.get("contractType") == "LinearPerpetual" \
                        and i.get("baseCoin") not in STABLE:
                    out[i["symbol"]] = status
            cur = r.get("nextPageCursor") or ""
            if not cur or not r["list"]:
                break
    json.dump(out, open(p, "w"))
    return out


def daily(sym):
    """{day_ms: (open, close, turnover)} from WARM on."""
    p = f"{CACHE}/d/{sym}.json"
    if not os.path.exists(p):
        bars, end = {}, ms(TODAY)
        while end > ms(WARM):
            lst = get("/v5/market/kline", {"category": "linear", "symbol": sym, "interval": "D",
                                           "start": ms(WARM), "end": end, "limit": 1000})["list"]
            new = [k for k in lst if int(k[0]) not in bars]
            if not new:
                break
            for k in new:
                bars[int(k[0])] = [float(k[1]), float(k[4]), float(k[6])]
            end = min(bars) - 1
        json.dump(bars, open(p, "w"))
    return {int(t): v for t, v in json.load(open(p)).items()}


def funding(sym, lo, hi):
    """Sum of funding rates settled in [lo, hi). Cached per symbol (whole history from WARM)."""
    p = f"{CACHE}/f/{sym}.json"
    if not os.path.exists(p):
        f, end = {}, ms(TODAY)
        while end > ms(WARM):
            lst = get("/v5/market/funding/history", {"category": "linear", "symbol": sym,
                                                     "startTime": ms(WARM), "endTime": end, "limit": 200})["list"]
            new = [k for k in lst if int(k["fundingRateTimestamp"]) not in f]
            if not new:
                break
            for k in new:
                f[int(k["fundingRateTimestamp"])] = float(k["fundingRate"])
            end = min(f) - 1
        json.dump(sorted(f.items()), open(p, "w"))
    if sym not in FUND:
        FUND[sym] = json.load(open(p))
    return sum(r for t, r in FUND[sym] if lo <= t < hi)


FUND = {}
INS = instruments()
with ThreadPoolExecutor(8) as ex:
    D = dict(zip(INS, ex.map(daily, INS)))
D = {s: b for s, b in D.items() if b}
print(f"instruments: {len(INS)} ({sum(v == 'Closed' for v in INS.values())} delisted), with bars: {len(D)}")


def pick(t):
    """Universe and legs at rebalance time t (ms), from bars strictly before t."""
    elig = []
    for s, b in D.items():
        if any((t - k * DAY) not in b for k in range(1, 31)):
            continue
        if sum(1 for d in b if d < t) < 90:
            continue
        elig.append((sum(b[t - k * DAY][2] for k in range(1, 31)), s))
    uni = sorted(s for _, s in sorted(elig, reverse=True)[:N_UNI])
    if len(uni) < N_UNI:
        return None
    score = {s: 0 for s in uni}  # integer rank sums: float thirds broke exact ties
    for h in (7, 14, 28):
        ret = {s: D[s][t - DAY][1] / D[s][t - (h + 1) * DAY][1] - 1 for s in uni}
        for rank, s in enumerate(sorted(uni, key=lambda s: (ret[s], s))):
            score[s] += rank
    order = sorted(uni, key=lambda s: (score[s], s))
    return uni, order[-N_LEG:], order[:N_LEG]


weeks, t, prev = [], ms(FIRST), {}
while t + 7 * DAY <= ms(TODAY):
    got = pick(t)
    if got:
        uni, longs, shorts = got
        w = {**{s: 0.5 / N_LEG for s in longs}, **{s: -0.5 / N_LEG for s in shorts}}

        def ret(s):
            b = D[s]
            if t not in b:
                return 0.0
            if t + 7 * DAY in b:
                return b[t + 7 * DAY][0] / b[t][0] - 1
            last = max(d for d in b if t <= d < t + 7 * DAY)
            return b[last][1] / b[t][0] - 1

        r = {s: ret(s) for s in set(w) | set(uni)}
        price = sum(w[s] * r[s] for s in w)
        turn = sum(abs(w.get(s, 0.0) - prev.get(s, 0.0)) for s in set(w) | set(prev))
        fund = sum(w[s] * funding(s, t, t + 7 * DAY) for s in w)  # longs pay a positive rate
        mkt = st.fmean(r[s] for s in uni)
        weeks.append({
            "t": t, "gross": price, "cost": COST * turn, "fund": fund,
            "net": price - COST * turn - fund, "mkt": mkt,
            "long": st.fmean(r[s] for s in longs) - mkt, "short": mkt - st.fmean(r[s] for s in shorts),
        })
        prev = {s: w[s] * (1 + r[s]) for s in w}
    else:
        prev = {}
    t += 7 * DAY


def pct(x):
    return f"{x * 100:+.3f}%"


net = [w["net"] for w in weeks]
n, m, sd = len(net), st.fmean(net), st.stdev(net)
se = sd / n ** 0.5
h1, h2 = net[:n // 2], net[n // 2:]
eq = peak = dd = 0.0
for x in net:
    eq += x
    peak = max(peak, eq)
    dd = min(dd, eq - peak)
mk = [w["mkt"] for w in weeks]
mm = st.fmean(mk)
corr = sum((a - m) * (b - mm) for a, b in zip(net, mk)) / ((n - 1) * sd * st.stdev(mk))  # py3.9 has no st.correlation
d0 = datetime.fromtimestamp(weeks[0]["t"] / 1000, tz=UTC).date()
d1 = datetime.fromtimestamp(weeks[-1]["t"] / 1000, tz=UTC).date()
print(f"weeks {n} ({d0} to {d1})")
print(f"net   mean {pct(m)}  sd {sd * 100:.2f}%  se {se * 100:.3f}%  bar {pct(Z * se)}  "
      f"90% bounds {pct(m - Z * se)} .. {pct(m + Z * se)}  Sharpe(ann) {m / sd * 52 ** 0.5:.2f}")
print(f"gross {pct(st.fmean(w['gross'] for w in weeks))}  cost {pct(st.fmean(w['cost'] for w in weeks))}  "
      f"funding {pct(st.fmean(w['fund'] for w in weeks))}  (per week)")
print(f"halves {pct(st.fmean(h1))} ({len(h1)}w)  {pct(st.fmean(h2))} ({len(h2)}w)   last 52w {pct(st.fmean(net[-52:]))}")
print(f"long leg vs universe {pct(st.fmean(w['long'] for w in weeks))}  short leg vs universe "
      f"{pct(st.fmean(w['short'] for w in weeks))}  universe {pct(st.fmean(mk))}  corr with universe {corr:+.2f}")
print(f"winning weeks {sum(x > 0 for x in net) / n:.0%}  total {pct(sum(net))}  worst drawdown {pct(dd)}")
print(f"80%-power edge at this sd: {pct((Z + 0.84) * se)} per week")
for y in range(d0.year, d1.year + 1):
    ys = [w["net"] for w in weeks if datetime.fromtimestamp(w["t"] / 1000, tz=UTC).year == y]
    print(f"  {y}: {len(ys)}w  mean {pct(st.fmean(ys))}  total {pct(sum(ys))}")
if n >= 150 and m > Z * se and st.fmean(h1) > 0 and st.fmean(h2) > 0:
    print("VERDICT: PASS")
elif m + Z * se < 0.0010:
    print("VERDICT: RULED OUT")
else:
    print("VERDICT: INCONCLUSIVE")
