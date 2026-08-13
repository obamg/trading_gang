"""ChainPulse — daily on-chain macro data via Santiment API."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import AsyncSessionLocal
from app.logging_config import log
from app.models.chainpulse import ChainPulseSnapshot
from app.services import redis_service

SANTIMENT_URL = "https://api.santiment.net/graphql"

ASSETS = ["bitcoin", "ethereum"]

# MVRV thresholds for regime classification
_REGIME_THRESHOLDS = [
    (3.7, "overheated"),
    (2.0, "distribution"),
    (0.8, "neutral"),
    (0.5, "accumulation"),
    (0.0, "deep_value"),
]


def _regime_from_mvrv(mvrv: float | None) -> str | None:
    if mvrv is None:
        return None
    for threshold, label in _REGIME_THRESHOLDS:
        if mvrv >= threshold:
            return label
    return "deep_value"


def _build_query(slug: str, from_iso: str, to_iso: str) -> str:
    return f"""
    {{
      mvrv: getMetric(metric: "mvrv_usd") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      nvt: getMetric(metric: "nvt") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      exchange_balance: getMetric(metric: "exchange_balance") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      exchange_inflow: getMetric(metric: "exchange_inflow") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      exchange_outflow: getMetric(metric: "exchange_outflow") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      active_addresses: getMetric(metric: "active_addresses_24h") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
      network_profit_loss: getMetric(metric: "network_profit_loss") {{
        timeseriesDataJson(slug: "{slug}", from: "{from_iso}", to: "{to_iso}", interval: "1d")
      }}
    }}
    """


def _latest_value(timeseries_json: str | None) -> float | None:
    """Extract the most recent non-null value from a Santiment timeseriesDataJson string."""
    if not timeseries_json:
        return None
    try:
        rows = json.loads(timeseries_json)
        if not isinstance(rows, list):
            return None
        for row in reversed(rows):
            v = row.get("value")
            if v is not None:
                return float(v)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def _latest_datetime(timeseries_json: str | None) -> datetime | None:
    """Extract the datetime of the most recent non-null point."""
    if not timeseries_json:
        return None
    try:
        rows = json.loads(timeseries_json)
        if not isinstance(rows, list):
            return None
        for row in reversed(rows):
            if row.get("value") is not None:
                dt_str = row.get("datetime")
                if dt_str:
                    return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


async def collect_asset(client: httpx.AsyncClient, asset: str, now: datetime) -> dict | None:
    # Query 60 days back to capture data even with the 30-day free-tier lag
    from_dt = (now - timedelta(days=60)).strftime("%Y-%m-%dT00:00:00Z")
    to_dt = now.strftime("%Y-%m-%dT00:00:00Z")

    headers = {"Content-Type": "application/graphql"}
    if settings.santiment_api_key:
        headers["Authorization"] = f"Apikey {settings.santiment_api_key}"

    try:
        resp = await client.post(
            SANTIMENT_URL,
            content=_build_query(asset, from_dt, to_dt),
            headers=headers,
        )
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        log.warning("chainpulse_fetch_failed", asset=asset, err=str(e))
        return None

    data = body.get("data") or {}

    mvrv = _latest_value((data.get("mvrv") or {}).get("timeseriesDataJson"))
    nvt = _latest_value((data.get("nvt") or {}).get("timeseriesDataJson"))
    exchange_balance = _latest_value((data.get("exchange_balance") or {}).get("timeseriesDataJson"))
    exchange_inflow = _latest_value((data.get("exchange_inflow") or {}).get("timeseriesDataJson"))
    exchange_outflow = _latest_value((data.get("exchange_outflow") or {}).get("timeseriesDataJson"))
    active_addresses = _latest_value((data.get("active_addresses") or {}).get("timeseriesDataJson"))
    network_profit_loss = _latest_value((data.get("network_profit_loss") or {}).get("timeseriesDataJson"))
    metric_date = _latest_datetime((data.get("mvrv") or {}).get("timeseriesDataJson")) or now

    regime = _regime_from_mvrv(mvrv)

    return {
        "asset": asset,
        "mvrv": mvrv,
        "nvt": nvt,
        "exchange_balance": exchange_balance,
        "exchange_inflow": exchange_inflow,
        "exchange_outflow": exchange_outflow,
        "active_addresses": active_addresses,
        "network_profit_loss": network_profit_loss,
        "regime": regime,
        "metric_date": metric_date,
    }


async def _cache_and_alert(asset: str, snapshot: dict, prev_regime: str | None) -> None:
    r = redis_service.get_redis()
    cache_payload = {k: (v if not isinstance(v, datetime) else v.isoformat()) for k, v in snapshot.items()}
    await r.set(f"chainpulse:latest:{asset}", json.dumps(cache_payload), ex=26 * 3600)
    await r.set(f"chainpulse:regime:{asset}", snapshot["regime"] or "unknown", ex=26 * 3600)

    regime = snapshot["regime"]
    mvrv = snapshot["mvrv"]
    if regime and prev_regime and regime != prev_regime:
        if regime == "overheated":
            await redis_service.publish_alert(
                "chainpulse",
                {
                    "module": "chainpulse",
                    "type": "mvrv_overheated",
                    "asset": asset,
                    "mvrv": mvrv,
                    "regime": regime,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        elif regime in ("accumulation", "deep_value") and prev_regime in ("neutral", "distribution", "overheated"):
            await redis_service.publish_alert(
                "chainpulse",
                {
                    "module": "chainpulse",
                    "type": "mvrv_undervalued",
                    "asset": asset,
                    "mvrv": mvrv,
                    "regime": regime,
                    "detected_at": datetime.now(timezone.utc).isoformat(),
                },
            )


async def run_daily_collection() -> None:
    if not settings.santiment_api_key:
        log.info("chainpulse_skipped", reason="no SANTIMENT_API_KEY configured")
        return

    now = datetime.now(timezone.utc)
    r = redis_service.get_redis()

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with AsyncSessionLocal() as db:
            for asset in ASSETS:
                prev_regime_raw = await r.get(f"chainpulse:regime:{asset}")
                prev_regime = prev_regime_raw.decode() if prev_regime_raw else None

                snapshot = await collect_asset(client, asset, now)
                if snapshot is None:
                    continue

                def _dec(v: float | None) -> Decimal | None:
                    return Decimal(str(round(v, 4))) if v is not None else None

                stmt = pg_insert(ChainPulseSnapshot).values(
                    asset=asset,
                    mvrv=_dec(snapshot["mvrv"]),
                    nvt=_dec(snapshot["nvt"]),
                    exchange_balance=_dec(snapshot["exchange_balance"]),
                    exchange_inflow=_dec(snapshot["exchange_inflow"]),
                    exchange_outflow=_dec(snapshot["exchange_outflow"]),
                    active_addresses=_dec(snapshot["active_addresses"]),
                    network_profit_loss=_dec(snapshot["network_profit_loss"]),
                    regime=snapshot["regime"],
                    metric_date=snapshot["metric_date"],
                    snapshot_at=now,
                ).on_conflict_do_update(
                    index_elements=["asset", "metric_date"],
                    set_={
                        "mvrv": _dec(snapshot["mvrv"]),
                        "nvt": _dec(snapshot["nvt"]),
                        "exchange_balance": _dec(snapshot["exchange_balance"]),
                        "exchange_inflow": _dec(snapshot["exchange_inflow"]),
                        "exchange_outflow": _dec(snapshot["exchange_outflow"]),
                        "active_addresses": _dec(snapshot["active_addresses"]),
                        "network_profit_loss": _dec(snapshot["network_profit_loss"]),
                        "regime": snapshot["regime"],
                        "snapshot_at": now,
                    },
                )
                await db.execute(stmt)
                await _cache_and_alert(asset, snapshot, prev_regime)
                log.info(
                    "chainpulse_collected",
                    asset=asset,
                    mvrv=snapshot["mvrv"],
                    regime=snapshot["regime"],
                    metric_date=snapshot["metric_date"].isoformat() if snapshot["metric_date"] else None,
                )

            await db.commit()
