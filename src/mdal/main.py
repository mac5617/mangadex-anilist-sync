"""Entry point: `uv run mdal`."""

import uvicorn

from mdal.config import get_settings
from mdal.logsetup import configure_logging
from mdal.web.app import create_app

# Loopback only (NFR-10). Deliberately not a setting.
HOST = "127.0.0.1"


def run() -> None:
    settings = get_settings()
    # Look settings up on every record so a token stored later (OAuth) is also masked.
    configure_logging(lambda: get_settings().secret_values())
    settings.ensure_db_dir()
    # log_config=None: uvicorn's loggers propagate to our redacting root handler.
    uvicorn.run(create_app(), host=HOST, port=settings.mdal_port, log_config=None)


if __name__ == "__main__":
    run()
