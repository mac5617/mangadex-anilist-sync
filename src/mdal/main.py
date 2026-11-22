"""Entry point: `uv run mdal`."""

import logging
import os
import sys

import uvicorn

from mdal.db.connection import MigrationCopyError
from mdal.logsetup import configure_logging
from mdal.services import Services
from mdal.web.app import create_app

# Loopback only (NFR-10). Deliberately not a setting.
HOST = "127.0.0.1"
BELOW_NORMAL_PRIORITY_CLASS = 0x4000

log = logging.getLogger(__name__)


def lower_priority() -> None:
    """Shiori mostly waits in the background; at below-normal priority its bursts (a sync, a scan) give way to
    whatever you're doing on the computer. Network pacing is unaffected."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), BELOW_NORMAL_PRIORITY_CLASS)
        else:
            os.nice(5)
    except Exception:   # never worth failing start-up over
        log.debug("could not lower the process priority", exc_info=True)


def run() -> None:
    try:
        services = Services.from_env()
    except MigrationCopyError as exc:
        sys.exit(str(exc))
    # Looked up on every record, so a token stored later (OAuth) is also masked.
    configure_logging(services.secret_values)
    lower_priority()
    # log_config=None: uvicorn's loggers propagate to our redacting root handler.
    uvicorn.run(create_app(services), host=HOST, port=services.settings.mdal_port, log_config=None)


if __name__ == "__main__":
    run()
