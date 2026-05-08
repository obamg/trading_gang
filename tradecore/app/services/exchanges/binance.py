"""Binance USD-M Futures adapter.

Validation:
    GET /sapi/v1/account/apiRestrictions — checks the key has futures-read
    enabled and refuses keys with withdrawal permission.

Fills:
    /fapi/v1/income gives a global income stream (no symbol filter), which we
    use to discover which symbols had activity in the window. For each active
    symbol we then call /fapi/v1/userTrades to pull fills in 7-day chunks.

Hedge mode (positionSide=LONG/SHORT) is collapsed into one-way semantics:
the pairing algorithm uses fill side, not positionSide.
"""
from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.logging_config import log
from app.services.exchanges.base import Credentials, Fill, register

BINANCE_REST = settings.binance_rest_url
INCOME_WINDOW_DAYS = 7
USER_TRADES_PAGE_LIMIT = 1000


class BinanceAuthError(RuntimeError):
    pass


class BinancePermissionError(RuntimeError):
    pass


def _sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def _signed_params(creds: Credentials, params: dict) -> dict:
    params = {**params, "timestamp": int(time.time() * 1000), "recvWindow": 10_000}
    query = urllib.parse.urlencode(params, doseq=True)
    params["signature"] = _sign(creds.api_secret, query)
    return params


async def _signed_get(
    client: httpx.AsyncClient, creds: Credentials, path: str, params: dict | None = None
) -> dict | list:
    headers = {"X-MBX-APIKEY": creds.api_key}
    resp = await client.get(
        f"{BINANCE_REST}{path}",
        params=_signed_params(creds, params or {}),
        headers=headers,
    )
    if resp.status_code == 401:
        raise BinanceAuthError(f"binance auth rejected: {resp.text}")
    if resp.status_code >= 400:
        raise RuntimeError(f"binance error {resp.status_code}: {resp.text}")
    return resp.json()


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class BinanceAdapter:
    name = "binance"
    display_name = "Binance Futures"
    requires_passphrase = False

    async def validate(self, creds: Credentials) -> dict:
        """Verify the key works AND has acceptable scope.

        We refuse keys with `enableWithdrawals=true` to bound blast radius even
        if the key is later compromised. Trading scope is allowed (users may
        not want to issue a separate read-only key) but is reported.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            restrictions = await _signed_get(
                client, creds, "/sapi/v1/account/apiRestrictions"
            )
            if not isinstance(restrictions, dict):
                raise BinancePermissionError("unexpected apiRestrictions payload")
            if restrictions.get("enableWithdrawals") is True:
                raise BinancePermissionError(
                    "API key has withdrawals enabled — please disable on Binance "
                    "before connecting."
                )
            if not restrictions.get("enableFutures"):
                raise BinancePermissionError(
                    "API key does not have Futures permission enabled."
                )
            # Sanity-check by hitting a futures endpoint.
            await _signed_get(client, creds, "/fapi/v2/account")
        return {
            "read": True,
            "futures": bool(restrictions.get("enableFutures")),
            "spot_trade": bool(restrictions.get("enableSpotAndMarginTrading")),
            "withdraw": bool(restrictions.get("enableWithdrawals")),
            "ip_restricted": bool(restrictions.get("ipRestrict")),
        }

    async def fetch_fills(self, creds: Credentials, since: datetime) -> list[Fill]:
        """Pull all USD-M futures fills since `since`, oldest first.

        Walks the income stream in 7-day pages to discover symbols, then pulls
        userTrades per symbol in 7-day pages. Both endpoints are rate-limited
        (1200 weight/min) so we keep each call cheap.
        """
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        symbols = await self._discover_symbols(creds, since=since, until=now)
        if not symbols:
            return []
        fills: list[Fill] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            for symbol in symbols:
                fills.extend(
                    await self._fetch_user_trades(client, creds, symbol, since=since, until=now)
                )
        fills.sort(key=lambda f: f.ts)
        log.info("binance_fills_fetched", count=len(fills), symbols=len(symbols))
        return fills

    async def _discover_symbols(
        self, creds: Credentials, *, since: datetime, until: datetime
    ) -> set[str]:
        """Use /fapi/v1/income to find symbols with activity."""
        symbols: set[str] = set()
        async with httpx.AsyncClient(timeout=15.0) as client:
            cursor = since
            while cursor < until:
                window_end = min(cursor + timedelta(days=INCOME_WINDOW_DAYS), until)
                rows = await _signed_get(
                    client,
                    creds,
                    "/fapi/v1/income",
                    {
                        "startTime": int(cursor.timestamp() * 1000),
                        "endTime": int(window_end.timestamp() * 1000),
                        "limit": 1000,
                    },
                )
                if not isinstance(rows, list):
                    break
                for r in rows:
                    sym = r.get("symbol")
                    if sym:
                        symbols.add(sym)
                cursor = window_end
        return symbols

    async def _fetch_user_trades(
        self,
        client: httpx.AsyncClient,
        creds: Credentials,
        symbol: str,
        *,
        since: datetime,
        until: datetime,
    ) -> list[Fill]:
        out: list[Fill] = []
        cursor = since
        while cursor < until:
            window_end = min(cursor + timedelta(days=INCOME_WINDOW_DAYS), until)
            rows = await _signed_get(
                client,
                creds,
                "/fapi/v1/userTrades",
                {
                    "symbol": symbol,
                    "startTime": int(cursor.timestamp() * 1000),
                    "endTime": int(window_end.timestamp() * 1000),
                    "limit": USER_TRADES_PAGE_LIMIT,
                },
            )
            if not isinstance(rows, list) or not rows:
                cursor = window_end
                continue
            for r in rows:
                out.append(_normalize_trade(r))
            # If we hit page limit, narrow the window from the last fill's time + 1ms.
            if len(rows) >= USER_TRADES_PAGE_LIMIT:
                last_ts_ms = int(rows[-1].get("time") or 0)
                if last_ts_ms <= 0:
                    cursor = window_end
                else:
                    cursor = datetime.fromtimestamp(
                        (last_ts_ms + 1) / 1000, tz=timezone.utc
                    )
            else:
                cursor = window_end
        return out


def _normalize_trade(r: dict) -> Fill:
    """Map a Binance userTrades row → canonical Fill."""
    side = "buy" if str(r.get("side", "")).upper() == "BUY" else "sell"
    ts = datetime.fromtimestamp(int(r.get("time", 0)) / 1000, tz=timezone.utc)
    fee = _to_float(r.get("commission"))
    fee_asset = r.get("commissionAsset")
    # Binance pays commission in the quote asset (typically USDT) for USD-M.
    # If it's BNB, the USD value isn't readily available from the trade row;
    # leave fee_usd=0 and let the user know to check.
    fee_usd = fee if fee_asset in ("USDT", "BUSD", "USDC", "FDUSD") else 0.0
    realized = _to_float(r.get("realizedPnl")) if r.get("realizedPnl") is not None else None
    return Fill(
        exchange="binance",
        exchange_trade_id=str(r.get("id")),
        exchange_order_id=str(r.get("orderId")) if r.get("orderId") is not None else None,
        symbol=str(r.get("symbol", "")).upper(),
        side=side,
        price=_to_float(r.get("price")),
        qty=_to_float(r.get("qty")),
        fee_usd=fee_usd,
        fee_asset=fee_asset,
        realized_pnl_usd=realized,
        is_reduce_only=bool(r.get("realizedPnl") and _to_float(r.get("realizedPnl")) != 0),
        ts=ts,
        raw=r,
    )


# Self-register on import
register(BinanceAdapter())

__all__ = ["BinanceAdapter"]
