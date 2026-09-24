"""Telegram onboarding — the one-tap connect flow.

The old flow made people copy a token between two apps, and never said which
bot to open. These tests pin the two things that fixed it: a deep link that
carries the code, and error copy that always names the next step.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.telegram_service import service as tg


class _Msg:
    def __init__(self) -> None:
        self.replies: list[str] = []

    async def reply_text(self, text, **kw):
        self.replies.append(text)


class _Update:
    """Minimal stand-in for telegram.Update."""

    def __init__(self, chat_id: int = 4242) -> None:
        self.message = _Msg()
        self.effective_chat = type("Chat", (), {"id": chat_id})()


class _Ctx:
    def __init__(self, args=None) -> None:
        self.args = args or []


# --- deep link -------------------------------------------------------------

def test_deep_link_carries_the_token():
    """The whole point: the URL contains the code, so the user copies nothing."""
    tg._bot_username = "TradeCoreBot"
    assert tg.deep_link("abc123") == "https://t.me/TradeCoreBot?start=abc123"


def test_deep_link_is_none_before_get_me():
    """get_me() can fail; callers must fall back to the manual code rather
    than render a broken t.me/None link."""
    tg._bot_username = None
    assert tg.deep_link("abc123") is None


# --- the shared link path --------------------------------------------------

@pytest.mark.asyncio
async def test_start_with_payload_links_immediately(monkeypatch):
    """`/start <token>` is what Telegram sends when the deep link is tapped.
    It must connect the account outright — that IS the one-tap flow."""
    seen = {}

    async def fake_link(update, token):
        seen["token"] = token

    monkeypatch.setattr(tg, "_link_user", fake_link)
    await tg._cmd_start(_Update(), _Ctx(["tok123"]))
    assert seen["token"] == "tok123"


@pytest.mark.asyncio
async def test_expired_token_explains_why_and_what_to_do(monkeypatch):
    """A dead end ('❌ Invalid or expired token.') is what made this hard.
    The message must say it expires, and where to get a fresh one."""
    async def no_token(_t):
        return None

    monkeypatch.setattr(tg, "_consume_link_token", no_token)
    upd = _Update()
    await tg._link_user(upd, "stale")
    reply = upd.message.replies[0]
    assert "10 minutes" in reply          # says WHY it failed
    assert "Settings" in reply            # says WHERE to fix it


@pytest.mark.asyncio
async def test_link_without_argument_points_at_the_button():
    """`Usage: /link <token>` told the user nothing about where a token comes
    from. The replacement must route them to the one-tap button."""
    upd = _Update()
    await tg._cmd_link(upd, _Ctx([]))
    reply = upd.message.replies[0]
    assert "Settings" in reply
    assert "Connect Telegram" in reply


class _NoRowSession:
    """Async-context session whose lookup finds nothing."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, _stmt):
        return type("Res", (), {"scalar_one_or_none": lambda self: None})()


@pytest.mark.asyncio
async def test_missing_settings_row_stays_human(monkeypatch):
    """'No settings row for this account' is developer-speak leaking to a user
    who cannot act on it."""
    async def some_user(_t):
        return uuid.uuid4()

    monkeypatch.setattr(tg, "_consume_link_token", some_user)
    monkeypatch.setattr(
        "app.services.telegram_service.AsyncSessionLocal", lambda: _NoRowSession()
    )
    upd = _Update()
    await tg._link_user(upd, "tok")
    reply = upd.message.replies[0]
    assert "settings row" not in reply.lower()
    assert "went wrong" in reply.lower()


# --- discoverability -------------------------------------------------------

@pytest.mark.asyncio
async def test_help_lists_every_command():
    """/status, /pause and /resume existed but were mentioned nowhere."""
    upd = _Update()
    await tg._cmd_help(upd, _Ctx())
    reply = upd.message.replies[0]
    for cmd in ("/status", "/pause", "/resume", "/start"):
        assert cmd in reply
