"""Start the Ember mobile API with ``python -m mobile.backend``."""
from __future__ import annotations

import logging
import shutil
import sys

from .config import ConfigurationError, Settings
from .http_api import VERSION, create_server


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        print(f"Ember API: {error}", file=sys.stderr)
        return 2

    missing = [tool for tool in ("ffmpeg", "node") if not shutil.which(tool)]
    if missing:
        print(
            "Ember API: missing required tool(s): "
            + ", ".join(missing)
            + ". Install FFmpeg and Node.js 20 or newer.",
            file=sys.stderr,
        )
        return 2

    server = create_server(settings)
    logging.getLogger("ember.api").info(
        "Ember API %s listening on %s:%s", VERSION, settings.host, settings.port
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
