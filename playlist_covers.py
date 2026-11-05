"""Private local cover images for playlists."""
import os
from pathlib import Path
import re

from services import DATA, load_private, save_private


COVERS_DIR = DATA / "playlist-covers"
INDEX_FILE = DATA / "playlist-covers.json"
MAX_COVERS = 200
MAX_COVER_BYTES = 8_000_000
_PLAYLIST_ID = re.compile(r"[\w-]{10,200}\Z", re.ASCII)


def _name(playlist_id):
    return f"{playlist_id}.png"


def _valid_id(playlist_id):
    return bool(_PLAYLIST_ID.fullmatch(str(playlist_id)))


def _clean_index(records, covers_dir):
    if not isinstance(records, dict):
        return {}
    cleaned = {}
    for raw_id, raw_name in records.items():
        playlist_id = str(raw_id)
        name = raw_name if isinstance(raw_name, str) else ""
        if not _valid_id(playlist_id) or name != _name(playlist_id):
            continue
        if not (covers_dir / name).is_file():
            continue
        cleaned[playlist_id] = name
        if len(cleaned) >= MAX_COVERS:
            break
    return cleaned


def load(path=INDEX_FILE, covers_dir=COVERS_DIR):
    try:
        data = load_private(path)
    except (OSError, ValueError):
        return {}
    records = data.get("covers", {}) if isinstance(data, dict) else {}
    return _clean_index(records, Path(covers_dir))


def save(path, covers, covers_dir=COVERS_DIR):
    cleaned = _clean_index(covers, Path(covers_dir))
    save_private(path, {"version": 1, "covers": cleaned})
    return cleaned


def path_for(playlist_id, covers, covers_dir=COVERS_DIR):
    if not _valid_id(playlist_id):
        return None
    name = (covers or {}).get(str(playlist_id))
    if name != _name(playlist_id):
        return None
    path = Path(covers_dir) / name
    return path if path.is_file() else None


def write(playlist_id, data, path=INDEX_FILE, covers_dir=COVERS_DIR):
    playlist_id = str(playlist_id)
    if not _valid_id(playlist_id):
        raise ValueError("Invalid playlist id")
    if not isinstance(data, (bytes, bytearray)) or not 0 < len(data) <= MAX_COVER_BYTES:
        raise ValueError("Invalid cover image")
    covers_dir = Path(covers_dir)
    covers_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(covers_dir, 0o700)
    except OSError:
        pass
    name = _name(playlist_id)
    target = covers_dir / name
    temp = covers_dir / f".{name}.tmp"
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out:
            out.write(bytes(data))
        os.replace(temp, target)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        temp.unlink(missing_ok=True)
        raise
    covers = load(path, covers_dir)
    covers[playlist_id] = name
    try:
        return save(path, covers, covers_dir)
    except Exception:
        target.unlink(missing_ok=True)
        raise


def remove(playlist_id, path=INDEX_FILE, covers_dir=COVERS_DIR):
    playlist_id = str(playlist_id)
    covers_dir = Path(covers_dir)
    covers = load(path, covers_dir)
    name = covers.pop(playlist_id, None)
    if name is None:
        return covers
    covers = save(path, covers, covers_dir)
    (covers_dir / name).unlink(missing_ok=True)
    return covers
