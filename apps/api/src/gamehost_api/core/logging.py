import logging
from collections.abc import MutableMapping
from typing import Any, cast

import structlog

_REDACT_KEYS = {
    "password",
    "token",
    "secret",
    "authorization",
    "cookie",
    "set-cookie",
    "gh_refresh",
    "refresh_token",
    "access_token",
    "secret_key",
}


def _walk(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in _REDACT_KEYS else _walk(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk(x) for x in obj]
    return obj


def redact_secrets(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    return cast(MutableMapping[str, Any], _walk(dict(event_dict)))


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(level=level, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            redact_secrets,
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )
