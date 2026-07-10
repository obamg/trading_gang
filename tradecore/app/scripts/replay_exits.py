"""Replay WaveBot exits against alternative exit rules (v1).

For every closed perp bot trade (or a JSON file of trade dicts), fetch the
5m klines covering entry → entry+24h from the trade's exchange, re-play the
exit under each variant in app/modules/bot/replay.py, and print per-variant
and per-(variant × direction) aggregates: n, win rate, avg / total net R,
median hold minutes.

Funding is NOT modeled in replay v1 — cascade-driven funding extremes
mean-revert quickly and per-interval historical rates aren't fetched here, so
long-hold variants (trail / time-stop survivors) look slightly better than
they would with funding included. Fees + slippage ARE modeled (see replay.py).

Modes:
  DB mode (default)   query bot_trades where status='closed' AND
                      market_type='perp'; optional --since ISO date.
  --json <path>       load a JSON array of trade dicts for offline runs:
                      [{"symbol": "XUSDT", "exchange": "bybit",
                        "direction": "long", "entry_price": 1.23,
                        "stop_price": 1.20, "entry_at": "2026-07-01T12:00:00Z"}]

Run with:
    docker compose exec api python -m app.scripts.replay_exits [--since 2026-06-01]
or offline:
    python -m app.scripts.replay_exits --json trades.json --out results.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from app.logging_config import configure_logging, log
from app.modules.bot import replay

BYBIT_KLINE_URL = "https://api.bybit.com/v5/market/kline"
BINANCE_PERP_KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
WINDOW_MS = 24 * 60 * 60 * 1000  # entry → entry+24h; 288 5m bars < limit=1000
REQUEST_SLEEP_S = 0.2  # be polite to public kline endpoints
_TIMEOUT_S = 10.0


# ---------- trade loading ----------


async def _load_db_trades(since: datetime | None) -> list[dict]:
    """Closed perp trades from bot_trades. Imports are local so --json mode
    runs without a reachable database."""
    from sqlalchemy import select

    from app.database import AsyncSessionLocal
    from app.models.bot import BotTrade

    async with AsyncSessionLocal() as db:
        stmt = select(BotTrade).where(
            BotTrade.status == "closed", BotTrade.market_type == "perp"
        )
        if since is not None:
            stmt = stmt.where(BotTrade.entry_at >= since)
        stmt = stmt.order_by(BotTrade.entry_at)
        rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "symbol": r.symbol,
            "exchange": r.exchange,
            "direction": r.direction,
            "entry_price": r.entry_price,
            "stop_price": r.stop_price,
            "entry_at": r.entry_at,
        }
        for r in rows
    ]


def _load_json_trades(path: Path, since: datetime | None) -> list[dict]:
    if not path.exists():
        raise SystemExit(f"JSON file not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise SystemExit("--json file must contain a JSON array of trade dicts")
    if since is not None:
        since_ms = int(since.timestamp() * 1000)
        data = [t for t in data if replay.to_epoch_ms(t["entry_at"]) >= since_ms]
    return data


# ---------- kline fetching ----------


def _bar_from_row(row: list) -> dict:
    """[ts, o, h, l, c, ...] → {t,o,h,l,c} (both venues share this prefix)."""
    return {
        "t": int(row[0]),
        "o": float(row[1]),
        "h": float(row[2]),
        "l": float(row[3]),
        "c": float(row[4]),
    }


async def _fetch_bybit_klines(
    client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int
) -> list[dict]:
    r = await client.get(
        BYBIT_KLINE_URL,
        params={
            "category": "linear",
            "symbol": symbol,
            "interval": "5",
            "start": start_ms,
            "end": end_ms,
            "limit": 1000,
        },
    )
    r.raise_for_status()
    data = r.json()
    if data.get("retCode") != 0:
        raise RuntimeError(f"bybit retCode={data.get('retCode')} {data.get('retMsg')}")
    rows = (data.get("result") or {}).get("list") or []
    # Bybit returns newest-first — reverse to chronological.
    return [_bar_from_row(row) for row in reversed(rows)]


async def _fetch_binance_klines(
    client: httpx.AsyncClient, symbol: str, start_ms: int, end_ms: int
) -> list[dict]:
    r = await client.get(
        BINANCE_PERP_KLINE_URL,
        params={
            "symbol": symbol,
            "interval": "5m",
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        },
    )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"binance unexpected payload: {data}")
    # Binance returns oldest-first — already chronological.
    return [_bar_from_row(row) for row in data]


async def _fetch_bars(client: httpx.AsyncClient, trade: dict) -> list[dict]:
    exchange = str(trade.get("exchange") or "bybit").lower()
    start_ms = replay.to_epoch_ms(trade["entry_at"])
    end_ms = start_ms + WINDOW_MS
    if exchange == "bybit":
        return await _fetch_bybit_klines(client, trade["symbol"], start_ms, end_ms)
    if exchange == "binance":
        return await _fetch_binance_klines(client, trade["symbol"], start_ms, end_ms)
    raise RuntimeError(f"unsupported exchange: {exchange}")


# ---------- result shaping ----------


def _jsonable(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


def _aggregate(per_trade: list[dict]) -> list[tuple]:
    """(variant, dir, n, win_rate, avg_net_r, total_net_r, median_hold) rows,
    dir ∈ all|long|short, in replay.VARIANTS order."""
    buckets: dict[tuple[str, str], list[dict]] = {}
    for t in per_trade:
        for res in t["variants"]:
            for dir_key in ("all", t["direction"]):
                buckets.setdefault((res["variant"], dir_key), []).append(res)
    rows = []
    for variant in replay.VARIANTS:
        for dir_key in ("all", "long", "short"):
            rs = buckets.get((variant, dir_key))
            if not rs:
                continue
            n = len(rs)
            wins = sum(1 for r in rs if r["net_r"] > 0)
            total_r = sum(r["net_r"] for r in rs)
            med_hold = statistics.median(r["hold_minutes"] for r in rs)
            rows.append((variant, dir_key, n, 100.0 * wins / n, total_r / n, total_r, med_hold))
    return rows


def _print_table(rows: list[tuple], n_trades: int, n_skipped: int) -> None:
    print(f"\nWaveBot exit replay — {n_trades} trades simulated, {n_skipped} skipped")
    print("(funding not modeled in v1; fees + slippage are)\n")
    header = (
        f"{'variant':<14} {'dir':<6} {'n':>5} {'win%':>7} "
        f"{'avg_R':>8} {'tot_R':>9} {'med_hold_m':>11}"
    )
    print(header)
    print("-" * len(header))
    prev_variant = None
    for variant, dir_key, n, win_rate, avg_r, total_r, med_hold in rows:
        if prev_variant is not None and variant != prev_variant:
            print()
        prev_variant = variant
        print(
            f"{variant:<14} {dir_key:<6} {n:>5} {win_rate:>6.1f}% "
            f"{avg_r:>+8.3f} {total_r:>+9.2f} {med_hold:>11.1f}"
        )
    print()


# ---------- main ----------


async def _run(args: argparse.Namespace) -> None:
    since: datetime | None = None
    if args.since:
        since = datetime.fromisoformat(args.since)
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)

    if args.json_path:
        trades = _load_json_trades(Path(args.json_path), since)
    else:
        trades = await _load_db_trades(since)
    log.info("replay_trades_loaded", n=len(trades), mode="json" if args.json_path else "db")
    if not trades:
        print("No trades to replay.", file=sys.stderr)
        return

    per_trade: list[dict] = []
    skipped = 0
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        for trade in trades:
            try:
                bars = await _fetch_bars(client, trade)
            except Exception as e:
                log.warning(
                    "replay_klines_failed",
                    symbol=trade.get("symbol"),
                    exchange=trade.get("exchange"),
                    err=str(e),
                )
                skipped += 1
                bars = []
            await asyncio.sleep(REQUEST_SLEEP_S)
            if not bars:
                continue
            variants = replay.run_all_variants(trade, bars)
            if not variants:
                log.warning(
                    "replay_no_usable_bars",
                    symbol=trade.get("symbol"),
                    entry_at=str(trade.get("entry_at")),
                )
                skipped += 1
                continue
            per_trade.append(
                {
                    "symbol": trade["symbol"],
                    "exchange": str(trade.get("exchange") or "bybit").lower(),
                    "direction": str(trade["direction"]).lower(),
                    "entry_at": _jsonable(trade["entry_at"]),
                    "entry_price": _jsonable(trade["entry_price"]),
                    "stop_price": _jsonable(trade["stop_price"]),
                    "variants": [_jsonable(v) for v in variants],
                }
            )

    log.info("replay_done", simulated=len(per_trade), skipped=skipped)
    if not per_trade:
        print("No trades produced results (all klines fetches failed?).", file=sys.stderr)
        return

    _print_table(_aggregate(per_trade), len(per_trade), skipped)

    if args.out:
        out_path = Path(args.out)
        with out_path.open("w", encoding="utf-8") as f:
            json.dump(per_trade, f, indent=2)
        print(f"Full per-trade results written to {out_path}")


async def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description="Replay closed WaveBot trades under alternative exit rules."
    )
    parser.add_argument(
        "--since", help="ISO date/datetime — only trades entered on/after this"
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        help="offline mode: path to a JSON array of trade dicts (skips the DB)",
    )
    parser.add_argument("--out", help="write full per-trade results JSON to this path")
    await _run(parser.parse_args())


if __name__ == "__main__":
    asyncio.run(main())
