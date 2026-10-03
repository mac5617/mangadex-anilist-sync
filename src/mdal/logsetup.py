"""Logging with secret redaction (NFR-11).

Redaction happens twice: a filter rewrites the message of every record, and the
formatter redacts the final text, which also covers tracebacks.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable

MASK = "***"
_MIN_SECRET_LEN = 4  # shorter values would mask unrelated text

_PATTERNS = [
    re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE),
    re.compile(
        r"""((?:access_token|refresh_token|password|client_secret)["']?\s*[=:]\s*["']?)[^\s&"',;}]+""",
        re.IGNORECASE,
    ),
]

SecretsProvider = Callable[[], Iterable[str]]


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in sorted((s for s in secrets if len(s) >= _MIN_SECRET_LEN), key=len, reverse=True):
        text = text.replace(secret, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + MASK, text)
    return text


class RedactingFilter(logging.Filter):
    def __init__(self, secrets: SecretsProvider) -> None:
        super().__init__()
        self._secrets = secrets

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message, self._secrets())
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: SecretsProvider, fmt: str | None = None) -> None:
        super().__init__(fmt)
        self._secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), self._secrets())


def configure_logging(secrets: SecretsProvider, level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(RedactingFilter(secrets))
    handler.setFormatter(RedactingFormatter(secrets, "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request lines at INFO; keep it quiet.
    logging.getLogger("httpx").setLevel(logging.WARNING)
