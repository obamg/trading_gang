"""Telegram fan-out sends once per CHAT, not once per settings row.

Two accounts linked to the same chat produced duplicate alerts in production.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import ws_manager as wm


class _Row:
    def __init__(self, user_id, chat_id):
        self.user_id = user_id
        self.telegram_chat_id = chat_id


class _Res:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return iter(self._rows)


class _DB:
    def __init__(self, rows): self._rows = rows
    async def execute(self, _stmt): return _Res(self._rows)


@pytest.mark.asyncio
async def test_same_chat_linked_twice_gets_one_message(monkeypatch):
    sent = []
    async def fake_send(chat_id, module, payload): sent.append(chat_id)
    monkeypatch.setattr(wm.telegram_service, "send_alert", fake_send)

    rows = [_Row("u1", "329185986"), _Row("u2", "329185986"), _Row("u3", "7756635153")]
    await wm.manager._deliver_telegram(_DB(rows), "oracle", {"symbol": "BTCUSDT"}, None)
    await asyncio.sleep(0)  # let create_task run
    assert sorted(sent) == [329185986, 7756635153]
