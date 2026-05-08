"""Exchange adapter framework.

Adapters live in this package and self-register into REGISTRY at import time.
The orchestrator (sync.py) and credentials router never import a specific
exchange — they look it up by name. Adding a new exchange is one new file.
"""
from __future__ import annotations

from app.services.exchanges.base import ExchangeAdapter, Fill, FillSide, REGISTRY, register

__all__ = ["ExchangeAdapter", "Fill", "FillSide", "REGISTRY", "register"]
