"""Private local storage shared by the desktop bridge and mobile backend."""
import json
import os
from pathlib import Path

DATA = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ember-player"
MAX_PRIVATE_BYTES = 4_000_000


def save_private(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out)
    os.replace(temp, path)


def load_private(path):
    """Read private JSON; missing, unreadable or malformed data is empty."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_PRIVATE_BYTES:
            return {}
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
