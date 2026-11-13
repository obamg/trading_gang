"""DayTrade playbook — the rules, as pure functions (no I/O, no DB, no Redis).

An opening-range breakout (ORB) with the risk rules discretionary day traders
and prop desks enforce:

  setup    first hour after a session open defines the range
  entry    first CLOSED 5m bar beyond the range (a wick is not an entry),
           filled at the NEXT bar's open plus slippage — the price a human
           acting on the alert could actually get
  filters  trigger bar volume >= 1.5x the prior 20 bars, and with the trend
           (close on the right side of the 20h EMA)
  stop     range midpoint. Refused when, after fees, the target would pay
           less than 1.5x the risk.
  target   2R. Time stop at session expiry — a day trade is flat by then.
  size     fixed-fraction: 1% of the account lost at the stop, fees included
  gate     max trades per session and per day, daily loss limit, cool-down
           after a losing streak

HONEST STATUS — read before trusting any of it:

- This is ORB-shaped, not the documented ORB. Crabel and Zarattini & Aziz
  studied equities with a real opening auction, a 5-minute range and a stop at
  the range extreme. Crypto's 00:00 UTC is a candle boundary, not a liquidity
  event; the midpoint stop and the EMA/volume filters are additions.
- A 90-day replay of THESE rules on Bybit (6 majors, 2026-08..11, risk gate
  applied, 236 trades) averaged -0.215R per trade: 33% winners, -50.8R total,
  and it failed the gate below in all three 60-trade blocks. Both sessions
  lost. The two filters let ~90% of sessions trigger, i.e. they barely filter.
  On that evidence the playbook has NO edge. Do not trade it with money.
- It is kept as a paper desk because the plumbing (range detection, sizing,
  risk gate, honest simulated fills, a gate that cannot be argued with) is the
  reusable part. A new rule set is a new `Playbook` and a new forward test —
  and the base rate in this repo is that such tests fail.

These numbers are deliberately NOT env-tunable. The repo's rule for anything
that trades (see CLAUDE.md, "No trading bots") is pre-commit, judge once,
disable rather than retune. Changing any constant changes `Playbook.version`,
which starts a new forward test from zero.

Bars are dicts {t, o, h, l, c, v}: t = open time in epoch ms, ascending.
"""
from __future__ import annotations

import hashlib
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BAR_MS = 5 * 60_000
VOLUME_LOOKBACK = 20
# A plan is only written when the trigger bar closed this recently. A scanner
# that boots mid-session must not "enter" at a price from hours ago.
FRESH_TRIGGER_MS = 2 * BAR_MS


@dataclass(frozen=True)
class Playbook:
    # "HH:MM@timezone". The US cash open is anchored in New York time so it
    # stays 09:30 ET across daylight saving (13:30 UTC in summer, 14:30 in
    # winter); a fixed UTC anchor would put the 08:30 ET data releases inside
    # the "opening range" for five months a year.
    sessions: tuple[str, ...] = ("00:00@UTC", "09:30@America/New_York")
    range_minutes: int = 60
    expire_hours: float = 8.0
    min_volume_x: float = 1.5
    trend_ema_bars: int = 240          # 20h of 5m bars
    target_r: float = 2.0
    min_net_rr: float = 1.5            # reward:risk after fees, at the target
    risk_pct: float = 1.0
    max_leverage: float = 5.0
    taker_fee_pct: float = 0.05        # per side
    entry_slippage_pct: float = 0.05   # adverse fill vs the next bar's open
    stop_slippage_pct: float = 0.05
    max_trades_per_session: int = 2    # majors break out together: cap the correlated basket
    max_trades_per_day: int = 4
    daily_loss_limit_r: float = 3.0
    max_consecutive_losses: int = 3
    # Forward-test gate, judged ONCE on the first `gate_n` closed plans: the
    # average net R must clear a one-sided 90% bound (gate_z * sd / sqrt(n)),
    # not merely zero. "Mean > 0" on 30 trades passes a zero-edge strategy
    # about half the time.
    gate_n: int = 60
    gate_z: float = 1.28

    @property
    def friction(self) -> float:
        """Round-trip fees + stop slippage, as a fraction of notional."""
        return (2 * self.taker_fee_pct + self.stop_slippage_pct) / 100.0

    @property
    def version(self) -> str:
        """Fingerprint of every rule. Stored on each plan; the scoreboard only
        counts plans made under the current one."""
        return hashlib.sha1(repr(sorted(asdict(self).items())).encode()).hexdigest()[:10]


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def session_start(now: datetime, spec: str) -> datetime:
    """Most recent occurrence of the session open at or before *now*, in UTC.

    *spec* is "HH:MM@timezone" (timezone defaults to UTC).
    """
    hhmm, _, tz = spec.partition("@")
    zone = ZoneInfo(tz or "UTC")
    h, m = map(int, hhmm.split(":"))
    local = now.astimezone(zone)
    a = local.replace(hour=h, minute=m, second=0, microsecond=0)
    if a > local:
        # Step back a calendar day in wall-clock time, then re-attach the zone,
        # so the result is still HH:MM local across a DST change.
        a = (a.replace(tzinfo=None) - timedelta(days=1)).replace(tzinfo=zone)
    return a.astimezone(timezone.utc)


def _ema(values: list[float], n: int) -> list[float]:
    k = 2.0 / (n + 1)
    out: list[float] = []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def evaluate(rows: list[dict], now: datetime, spec: str, pb: Playbook) -> dict:
    """State of one session's setup: forming | waiting | triggered | missed | expired."""
    now_ms = _ms(now)
    closed = [r for r in rows if r["t"] + BAR_MS <= now_ms]
    anchor = session_start(now, spec)
    rend = anchor + timedelta(minutes=pb.range_minutes)
    expire = anchor + timedelta(hours=pb.expire_hours)
    need = pb.range_minutes * 60_000 // BAR_MS
    rng = [r for r in closed if _ms(anchor) <= r["t"] < _ms(rend)]
    res: dict = {
        "session": spec,
        "anchor": anchor.isoformat(),
        "range_end": rend.isoformat(),
        "expires": expire.isoformat(),
        "last_price": rows[-1]["c"] if rows else None,
        "bars_closed": len(rng),
        "bars_needed": need,
        "skipped": [],
    }
    if rng:
        res["range_high"] = max(r["h"] for r in rng)
        res["range_low"] = min(r["l"] for r in rng)
    if len(rng) < need:
        # Also covers a data gap: a range missing bars is not a range.
        res["status"] = "forming" if now < expire else "expired"
        return res

    hi, lo = res["range_high"], res["range_low"]
    ema = _ema([r["c"] for r in closed], pb.trend_ema_bars)
    for i, r in enumerate(closed):
        if not (_ms(rend) <= r["t"] < _ms(expire)):
            continue
        side = "LONG" if r["c"] > hi else "SHORT" if r["c"] < lo else None
        if side is None:
            continue
        prior = closed[max(0, i - VOLUME_LOOKBACK):i]
        avg_v = sum(x["v"] for x in prior) / len(prior) if prior else 0.0
        vol_x = r["v"] / avg_v if avg_v else 0.0
        trend = ema[i - 1] if i else r["c"]
        with_trend = r["c"] > trend if side == "LONG" else r["c"] < trend
        if vol_x < pb.min_volume_x or not with_trend:
            why = []
            if vol_x < pb.min_volume_x:
                why.append(f"volume {vol_x:.2f}x < {pb.min_volume_x:g}x")
            if not with_trend:
                why.append("against the 20h trend")
            res["skipped"].append({"t": r["t"], "side": side, "close": r["c"], "why": ", ".join(why)})
            continue
        # Fill at the next bar's open (known the instant the trigger bar
        # closes, so no look-ahead), moved against us by the entry slippage.
        nxt = next((x for x in rows if x["t"] == r["t"] + BAR_MS), None)
        s = 1 if side == "LONG" else -1
        entry = (nxt["o"] if nxt else r["c"]) * (1 + s * pb.entry_slippage_pct / 100.0)
        fresh = now_ms - (r["t"] + BAR_MS) <= FRESH_TRIGGER_MS
        res.update({
            "status": "triggered" if fresh else "missed",
            "side": side,
            "entry": float(f"{entry:.8g}"),
            "trigger_close_ms": r["t"] + BAR_MS,
            "vol_x": round(vol_x, 2),
            "trend_ema": trend,
        })
        return res

    res["status"] = "expired" if now >= expire else "waiting"
    return res


def build_plan(side: str, entry: float, range_high: float, range_low: float, pb: Playbook) -> tuple[dict, list[str]]:
    """Stop at the range midpoint, target at `target_r`. Returns (plan, refusals)."""
    s = 1 if side == "LONG" else -1
    stop = (range_high + range_low) / 2.0
    risk = s * (entry - stop)
    reasons: list[str] = []
    if risk <= 0:
        return {}, [f"stop {stop:g} is on the wrong side of entry {entry:g}"]
    stop_frac = risk / entry
    net_rr = (pb.target_r * stop_frac - 2 * pb.taker_fee_pct / 100.0) / (stop_frac + pb.friction)
    if net_rr < pb.min_net_rr:
        reasons.append(
            f"stop is only {stop_frac * 100:.2f}% from entry: after fees the target pays "
            f"{net_rr:.2f}x the risk, under the {pb.min_net_rr:g}x minimum"
        )
    # ponytail: 8 significant digits strips float noise; it is not exchange
    # tick rounding. Round to the symbol's tickSize if orders are ever built from this.
    return {
        "side": side,
        "entry": entry,
        "stop": float(f"{stop:.8g}"),
        "target": float(f"{entry + s * pb.target_r * risk:.8g}"),
        "stop_pct": stop_frac * 100.0,
        "net_rr": round(net_rr, 2),
    }, reasons


def gate(symbol: str, day: dict, pb: Playbook) -> list[str]:
    """Risk gate. *day* = {trades, session_trades, loss_r, open_symbols, streak} for today (UTC)."""
    reasons: list[str] = []
    if day.get("session_trades", 0) >= pb.max_trades_per_session:
        reasons.append(f"already {day['session_trades']} trades this session (max {pb.max_trades_per_session})")
    if day["trades"] >= pb.max_trades_per_day:
        reasons.append(f"already {day['trades']} trades today (max {pb.max_trades_per_day})")
    if day["streak"] >= pb.max_consecutive_losses:
        reasons.append(f"{day['streak']} losses in a row: cool-down until tomorrow (UTC)")
    used = day["loss_r"] + len(day["open_symbols"]) + 1
    if used > pb.daily_loss_limit_r + 1e-9:
        reasons.append(
            f"today's losses {day['loss_r']:.1f}R + open risk {len(day['open_symbols'])}R "
            f"+ this 1R is over the {pb.daily_loss_limit_r:g}R daily limit"
        )
    if symbol in day["open_symbols"]:
        reasons.append(f"a {symbol} plan is already open")
    return reasons


def net_r(side: str, entry: float, stop: float, exit_price: float, stopped: bool, pb: Playbook) -> float:
    """Result in R, net of fees (and slippage on a stop-out).

    1R is the sized loss at the stop INCLUDING friction, so a stop filled at
    the stop price is exactly -1.00R, a stop gapped through is worse, and a 2R
    target nets less than 2.

    ponytail: funding paid across a settlement is not charged (~0.01-0.03R
    against, on the US session). Add it if the record is ever used for more
    than a pass/fail read.
    """
    s = 1 if side == "LONG" else -1
    stop_frac = s * (entry - stop) / entry
    gross = s * (exit_price - entry) / entry
    cost = (2 * pb.taker_fee_pct + (pb.stop_slippage_pct if stopped else 0.0)) / 100.0
    return (gross - cost) / (stop_frac + pb.friction)


def resolve(
    side: str, entry: float, stop: float, target: float,
    triggered_ms: int, exit_by: datetime, rows: list[dict], now: datetime, pb: Playbook,
) -> tuple[str, float, float] | None:
    """Paper outcome from closed bars from the entry bar on: (status, exit, net R) or None if still open.

    Pessimistic on every ambiguity: a bar touching both stop and target is the
    stop; a bar that opens beyond the stop fills at that open, not at the stop;
    and the target needs a print THROUGH it, since a touch does not fill a
    resting order.
    """
    now_ms, end_ms = _ms(now), _ms(exit_by)
    bars = [r for r in rows if triggered_ms <= r["t"] < end_ms and r["t"] + BAR_MS <= now_ms]
    long = side == "LONG"
    for r in bars:
        if (r["l"] <= stop) if long else (r["h"] >= stop):
            px = min(r["o"], stop) if long else max(r["o"], stop)
            return "stop", px, net_r(side, entry, stop, px, True, pb)
        if (r["h"] > target) if long else (r["l"] < target):
            return "target", target, net_r(side, entry, stop, target, False, pb)
    if now >= exit_by:
        px = bars[-1]["c"] if bars else entry
        return "time", px, net_r(side, entry, stop, px, False, pb)
    return None


def scoreboard(results: list[float], pb: Playbook) -> dict:
    """Forward-test summary over closed plans, oldest first.

    The verdict is judged once, on the first `gate_n` results only — later
    trades can't rescue it — and passing takes a margin, not just a positive
    average: mean > gate_z * sd / sqrt(n).
    """
    n = len(results)
    verdict, bar = "collecting", None
    if n >= pb.gate_n:
        judged = results[: pb.gate_n]
        bar = pb.gate_z * statistics.stdev(judged) / pb.gate_n ** 0.5
        verdict = "passed" if statistics.fmean(judged) > bar else "failed"
    curve, total = [], 0.0
    for r in results:
        total += r
        curve.append(round(total, 3))
    return {
        "n": n,
        "gate_n": pb.gate_n,
        "wins": sum(1 for r in results if r > 0),
        "avg_r": round(total / n, 3) if n else None,
        "total_r": round(total, 3),
        "curve": curve,
        "verdict": verdict,
        "bar": round(bar, 3) if bar is not None else None,
    }
