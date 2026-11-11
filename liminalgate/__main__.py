"""``python -m liminalgate``"""

from __future__ import annotations

import uvicorn

from liminalgate.logging_config import configure_logging
from liminalgate.settings import load_settings


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)
    uvicorn.run(
        "liminalgate.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
