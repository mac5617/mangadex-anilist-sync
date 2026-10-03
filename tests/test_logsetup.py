import logging

import pytest

from mdal.logsetup import MASK, RedactingFilter, RedactingFormatter, redact

SECRETS = ["anilist-token-123456", "Pa55word!", "md-client-secret-xyz"]


@pytest.fixture
def capture():
    """A logger wired exactly like configure_logging, writing to a list."""
    lines: list[str] = []

    class ListHandler(logging.Handler):
        def emit(self, record):
            lines.append(self.format(record))

    handler = ListHandler()
    handler.addFilter(RedactingFilter(lambda: SECRETS))
    handler.setFormatter(RedactingFormatter(lambda: SECRETS, "%(message)s"))
    logger = logging.getLogger("test.redaction")
    logger.handlers[:] = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    yield logger, lines
    logger.handlers.clear()


@pytest.mark.parametrize("secret", SECRETS)
def test_configured_secret_values_are_masked(capture, secret):
    logger, lines = capture
    logger.info("value is %s here", secret)
    assert secret not in lines[0]
    assert MASK in lines[0]


@pytest.mark.parametrize(
    "text, leaked",
    [
        ("Authorization: Bearer eyJhbGciOi.abc.def", "eyJhbGciOi.abc.def"),
        ("grant_type=password&username=me&password=s3cret&client_id=x", "s3cret"),
        ('{"access_token": "abc123token", "expires_in": 900}', "abc123token"),
        ("refresh_token=rt-998877&client_secret=cs-445566", "rt-998877"),
        ("refresh_token=rt-998877&client_secret=cs-445566", "cs-445566"),
    ],
)
def test_patterns_are_masked_even_when_not_configured(text, leaked):
    out = redact(text)
    assert leaked not in out
    assert MASK in out


def test_traceback_text_is_masked(capture):
    logger, lines = capture
    try:
        raise RuntimeError(f"failed with token {SECRETS[0]}")
    except RuntimeError:
        logger.exception("boom")
    assert SECRETS[0] not in lines[0]
    assert "RuntimeError" in lines[0]


def test_ordinary_text_is_untouched():
    assert redact("Fetched 120 manga in 3 requests", SECRETS) == "Fetched 120 manga in 3 requests"


def test_tiny_secret_values_are_ignored():
    assert redact("a b c", ["a"]) == "a b c"
