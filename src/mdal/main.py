"""Entry point: `uv run mdal`."""

import uvicorn

from mdal.logsetup import configure_logging
from mdal.services import Services
from mdal.web.app import create_app

# Loopback only (NFR-10). Deliberately not a setting.
HOST = "127.0.0.1"


def run() -> None:
    services = Services.from_env()
    # Looked up on every record, so a token stored later (OAuth) is also masked.
    configure_logging(services.secret_values)
    # log_config=None: uvicorn's loggers propagate to our redacting root handler.
    uvicorn.run(create_app(services), host=HOST, port=services.settings.mdal_port, log_config=None)


if __name__ == "__main__":
    run()
