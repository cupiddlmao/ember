"""Metadata-only local playlist bookmarks; no account sync or media URLs."""
import re

import artwork
from music import music_link
from services import load_private, save_private


MAX_SAVED_PLAYLISTS = 200


def _playlist_id(record):
    value = record.get("id")
    if value:
        return str(value)
    uri = record.get("uri")
    if isinstance(uri, str):
        try:
            video, playlist = music_link(uri)
        except ValueError:
            return None
        if playlist and not video:
            return playlist
    return None


def _images(record):
    values = record.get("images") or []
    if isinstance(values, (str, dict)):
        values = [values]
    if not isinstance(values, (list, tuple)):
        return []
    images, seen = [], set()
    for item in values[:8]:
        url = item.get("url") if isinstance(item, dict) else item
        if artwork.artwork_url(url) is None or url in seen:
            continue
        image = {"url": url}
        if isinstance(item, dict):
            for key in ("width", "height"):
                try:
                    value = int(item.get(key) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value > 0:
                    image[key] = value
        images.append(image)
        seen.add(url)
    return images


def clean(record):
    """Return the small metadata record allowed on disk, or None if invalid."""
    if not isinstance(record, dict):
        return None
    playlist_id = _playlist_id(record)
    if not playlist_id or not re.fullmatch(r"[\w-]{10,200}", playlist_id, flags=re.ASCII):
        return None
    uri = "https://music.youtube.com/playlist?list=" + playlist_id
    try:
        video, parsed = music_link(uri)
    except ValueError:
        return None
    if video or parsed != playlist_id:
        return None
    title = str(record.get("title") or record.get("name") or "YouTube Music playlist").strip()[:200]
    owner = record.get("owner") or ""
    if isinstance(owner, dict):
        owner = owner.get("name") or ""
    owner = str(owner).strip()[:120]
    try:
        count = max(0, min(100000, int(record.get("track_count") or 0)))
    except (TypeError, ValueError):
        count = 0
    return {
        "id": playlist_id,
        "title": title or "YouTube Music playlist",
        "owner": owner,
        "uri": uri,
        "images": _images(record),
        "track_count": count,
    }


def _cleaned(records):
    result, seen = [], set()
    for record in records if isinstance(records, list) else []:
        value = clean(record)
        if value and value["id"] not in seen:
            result.append(value)
            seen.add(value["id"])
        if len(result) >= MAX_SAVED_PLAYLISTS:
            break
    return result


def load(path):
    try:
        data = load_private(path)
    except (OSError, ValueError):
        return []
    records = data.get("playlists", []) if isinstance(data, dict) else []
    return _cleaned(records)


def save(path, records):
    cleaned = _cleaned(records)
    save_private(path, {"version": 1, "playlists": cleaned})
    return cleaned


def add(records, record):
    value = clean(record)
    if not value:
        return _cleaned(records)
    return [value] + [item for item in _cleaned(records) if item["id"] != value["id"]]


def remove(records, playlist_id):
    return [item for item in _cleaned(records) if item["id"] != str(playlist_id)]
