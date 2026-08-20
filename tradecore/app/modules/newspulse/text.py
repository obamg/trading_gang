"""Text primitives shared by the NewsPulse collectors.

Extracted so ``announcements.py`` (primary sources) and ``collector.py``
(media RSS) can share matching + parsing without a circular import. Import
direction is one-way: text ← announcements ← collector.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from app.logging_config import log

HTML_TAG_RE = re.compile(r"<[^>]+>")


def compile_terms(terms: Iterable[str]) -> re.Pattern[str]:
    """Build a word-boundary alternation over a term set.

    Terms are ordered longest-first so a multi-word phrase wins over its own
    constituents ("all-time high" is consumed before "high" can match it).

    Always use this instead of ``word in text``: the substring form made
    ``ath`` fire on "gather", ``ban`` on "banking" and ``sec`` on "second",
    which tagged 45% of all articles high-impact.
    """
    ordered = sorted({t.lower() for t in terms}, key=len, reverse=True)
    joined = "|".join(re.escape(t) for t in ordered)
    return re.compile(rf"\b(?:{joined})\b", re.IGNORECASE)


def distinct_hits(pattern: re.Pattern[str], text: str) -> int:
    """Count *distinct* matched terms, so one word repeated is worth one hit."""
    return len({m.group(0).lower() for m in pattern.finditer(text)})


def strip_html(text: str) -> str:
    if not text:
        return ""
    return HTML_TAG_RE.sub("", text).strip()


def parse_pub_date(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return datetime.now(timezone.utc)


def source_id_for(key: str) -> str:
    """sha1 → 40 hex chars: stable across runs, fits source_id varchar(64)."""
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def parse_rss_xml(xml_text: str, source_name: str) -> list[dict]:
    """Parse an RSS 2.0 feed body into a list of raw article dicts."""
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("newspulse_rss_parse_failed", source=source_name, err=str(e))
        return items

    channel = root.find("channel")
    if channel is None:
        return items

    for item in channel.findall("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        if not title or not link:
            continue

        items.append({
            "id": source_id_for(guid),
            "title": title,
            "description": strip_html(item.findtext("description") or ""),
            "url": link,
            "source_name": source_name,
            "published_at": parse_pub_date(item.findtext("pubDate")),
        })
    return items
