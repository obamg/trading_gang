"""Bybit V5 adapter — Linear (USDT-perp) execution history.

Validation:
    GET /v5/user/query-api — returns `permissions` with categories like
    "ContractTrade", "Wallet" etc. We refuse keys with any "Withdraw" entry.

Fills:
    GET /v5/execution/list?category=linear — paginated by `cursor`. Bybit
    requires startTime within 7 days of endTime, so we walk in 7-day windows
    just like Binance.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

import httpx

from app.logging_config import log
from app.services.exchanges.base import Credentials, Fill, register

BYBIT_REST = "https://api.bybit.com"
EXEC_WINDOW_DAYS = 7
EXEC_PAGE_LIMIT = 100  # Bybit V5 max for execution/list


class BybitAuthError(RuntimeError):
    pass


class BybitPermissionError(RuntimeError):
    pass


def _sign(secret: str, timestamp: str, api_key: str, recv_window: str, payload: str) -> str:
    raw = f"{timestamp}{api_key}{recv_window}{payload}"
    return hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()


def _headers(creds: Credentials, payload: str) -> dict:
    timestamp = str(int(time.time() * 1000))
    recv_window = "10000"
    return {
        "X-BAPI-API-KEY": creds.api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": _sign(creds.api_secret, timestamp, creds.api_key, recv_window, payload),
        "X-BAPI-SIGN-TYPE": "2",
    }


async def _signed_get(
    client: httpx.AsyncClient, creds: Credentials, path: str, params: dict | None = None
) -> dict:
    params = params or {}
    # Sign the same byte string we send. httpx serializes `params=` in dict
    # insertion order, so we pre-encode (sorted) and append to the URL — the
    # signed payload and the wire query string are guaranteed identical.
    query = urllib.parse.urlencode(sorted(params.items()))
    url = f"{BYBIT_REST}{path}"
    if query:
        url = f"{url}?{query}"
    resp = await client.get(url, headers=_headers(creds, query))
    if resp.status_code == 401 or resp.status_code == 403:
        raise BybitAuthError(f"bybit auth rejected: {resp.text}")
    if resp.status_code >= 400:
        raise RuntimeError(f"bybit error {resp.status_code}: {resp.text}")
    body = resp.json()
    # Bybit always returns `retCode` — non-zero is an application-level error.
    if body.get("retCode") not in (0, "0"):
        msg = body.get("retMsg") or "unknown bybit error"
        if "permission" in msg.lower() or body.get("retCode") in (10005, 10003):
            raise BybitPermissionError(msg)
        raise RuntimeError(f"bybit retCode={body.get('retCode')}: {msg}")
    return body.get("result") or {}


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class BybitAdapter:
    name = "bybit"
    display_name = "Bybit Linear Perpetuals"
    requires_passphrase = False

    async def validate(self, creds: Credentials) -> dict:
        async with httpx.AsyncClient(timeout=10.0) as client:
            info = await _signed_get(client, creds, "/v5/user/query-api")
            permissions = info.get("permissions") or {}
            # Refuse keys with any withdraw permission.
            withdraw_perms = permissions.get("Wallet") or []
            if any("Withdraw" in str(p) for p in withdraw_perms):
                raise BybitPermissionError(
                    "API key has withdrawal permission — please disable on Bybit "
                    "before connecting."
                )
            contract = permissions.get("ContractTrade") or []
            derivatives = permissions.get("Derivatives") or []
            if not (contract or derivatives):
                raise BybitPermissionError(
                    "API key does not have Derivatives/Contract permission."
                )
        return {
            "read": True,
            "futures": True,
            "withdraw": False,
            "ip_restricted": bool(info.get("ips")),
            "raw_permissions": permissions,
        }

    async def fetch_fills(self, creds: Credentials, since: datetime) -> list[Fill]:
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        out: list[Fill] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            cursor_time = since
            while cursor_time < now:
                window_end = min(cursor_time + timedelta(days=EXEC_WINDOW_DAYS), now)
                page_cursor: str | None = None
                while True:
                    params = {
                        "category": "linear",
                        "startTime": int(cursor_time.timestamp() * 1000),
                        "endTime": int(window_end.timestamp() * 1000),
                        "limit": EXEC_PAGE_LIMIT,
                    }
                    if page_cursor:
                        params["cursor"] = page_cursor
                    result = await _signed_get(
                        client, creds, "/v5/execution/list", params
                    )
                    rows = result.get("list") or []
                    for r in rows:
                        out.append(_normalize_exec(r))
                    page_cursor = result.get("nextPageCursor") or None
                    if not page_cursor or not rows:
                        break
                cursor_time = window_end
        out.sort(key=lambda f: f.ts)
        log.info("bybit_fills_fetched", count=len(out))
        return out


def _normalize_exec(r: dict) -> Fill:
    """Map a Bybit V5 execution row → canonical Fill."""
    side = "buy" if str(r.get("side", "")).lower() == "buy" else "sell"
    ts_ms = int(r.get("execTime") or r.get("execTimestamp") or 0)
    ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc) if ts_ms else datetime.now(timezone.utc)
    fee = _to_float(r.get("execFee"))
    fee_asset = r.get("feeCurrency") or "USDT"
    fee_usd = fee if fee_asset in ("USDT", "USDC") else 0.0
    closed_size = _to_float(r.get("closedSize"))
    realized = _to_float(r.get("execPnl")) if r.get("execPnl") is not None else None
    return Fill(
        exchange="bybit",
        exchange_trade_id=str(r.get("execId")),
        exchange_order_id=str(r.get("orderId")) if r.get("orderId") else None,
        symbol=str(r.get("symbol", "")).upper(),
        side=side,
        price=_to_float(r.get("execPrice")),
        qty=_to_float(r.get("execQty")),
        fee_usd=fee_usd,
        fee_asset=fee_asset,
        realized_pnl_usd=realized,
        is_reduce_only=closed_size > 0,
        ts=ts,
        raw=r,
    )


register(BybitAdapter())

__all__ = ["BybitAdapter"]
