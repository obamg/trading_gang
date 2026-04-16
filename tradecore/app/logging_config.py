"""structlog JSON logging, with redaction for secrets."""
import logging
import sys

import structlog

_SENSITIVE_KEYS = {"password", "password_hash", "token", "access_token",
                   "refresh_token", "authorization", "api_key", "secret"}


def _redact(_logger, _method, event_dict: dict) -> dict:
    for k in list(event_dict.keys()):
        if k.lower() in _SENSITIVE_KEYS:
            event_dict[k] = "***REDACTED***"
    return event_dict


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_log_level,
            _redact,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger()
