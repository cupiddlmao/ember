"""Public and unlisted YouTube Music playlist previews for the iPhone app.

Only the existing anonymous catalog path is used. Account playlists, Google
cookies, and private library sync are deliberately out of scope.
"""
from __future__ import annotations

import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import music  # noqa: E402  (path setup must happen first)
import playlist_search  # noqa: E402

from .music_service import UpstreamError, serialize_track  # noqa: E402

PLAYLIST_ID = re.compile(r"^[\w-]{10,200}$", re.ASCII)
MAX_PLAYLIST_URL_CHARS = 2048
MAX_PLAYLIST_TRACKS = 5000
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 50
MAX_QUERY_CHARS = 200
CACHE_TTL_SECONDS = 15 * 60
CACHE_ITEMS = 8


class PlaylistInputError(ValueError):
    """Invalid user input, safe to return as a 400 without upstream details."""


@dataclass(frozen=True)
class _LoadedPlaylist:
    summary: dict[str, Any]
    tracks: list[dict[str, Any]]
    search_records: list[dict[str, Any]]
    fetched_at: float


def _best_artwork(images: Any) -> str | None:
    if isinstance(images, dict):
        images = [images]
    if not isinstance(images, (list, tuple)):
        return None
    best: tuple[int, str] | None = None
    for image in images[:12]:
        url = image.get("url") if isinstance(image, dict) else image
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        width = height = 0
        if isinstance(image, dict):
            try:
                width = int(image.get("width") or 0)
                height = int(image.get("height") or 0)
            except (TypeError, ValueError):
                width = height = 0
        area = max(0, width) * max(0, height)
        if best is None or area >= best[0]:
            best = (area, url)
    return best[1] if best else None


def _search_record(track: dict[str, Any]) -> dict[str, Any]:
    """Adapt the mobile JSON shape to the desktop playlist_search helper."""
    return {
        "name": track["title"],
        "artists": [{"name": track["artist"]}],
        "album": {"name": track["album"]},
    }


class PlaylistService:
    def __init__(
        self,
        max_tracks: int = MAX_PLAYLIST_TRACKS,
        cache_ttl: float = CACHE_TTL_SECONDS,
        cache_items: int = CACHE_ITEMS,
    ):
        self.max_tracks = max(1, int(max_tracks))
        self.cache_ttl = max(0.0, float(cache_ttl))
        self.cache_items = max(1, int(cache_items))
        self._cache: OrderedDict[str, _LoadedPlaylist] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def preview(self, url: str, limit: int = DEFAULT_PAGE_SIZE) -> dict[str, Any]:
        value = str(url or "").strip()
        if not value:
            raise PlaylistInputError("Paste a public or unlisted YouTube Music playlist link.")
        if len(value) > MAX_PLAYLIST_URL_CHARS:
            raise PlaylistInputError("That playlist link is too long.")
        try:
            _video, playlist_id = music.music_link(value)
        except ValueError as error:
            raise PlaylistInputError(str(error)) from None
        if not playlist_id:
            raise PlaylistInputError("Paste a playlist link, not a single song link.")
        return self.page(playlist_id, 0, limit)

    def page(
        self,
        playlist_id: str,
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
        query: str = "",
    ) -> dict[str, Any]:
        playlist_id = str(playlist_id or "").strip()
        if not PLAYLIST_ID.fullmatch(playlist_id):
            raise PlaylistInputError("Invalid playlist ID.")
        try:
            offset = max(0, int(offset))
        except (TypeError, ValueError):
            raise PlaylistInputError("The playlist offset must be a whole number.") from None
        try:
            limit = max(1, min(MAX_PAGE_SIZE, int(limit)))
        except (TypeError, ValueError):
            raise PlaylistInputError("The playlist page size must be a whole number.") from None

        needle = " ".join(str(query or "").split())
        if len(needle) > MAX_QUERY_CHARS:
            raise PlaylistInputError("Playlist searches are limited to 200 characters.")

        loaded = self._load(playlist_id)
        if needle:
            matches = playlist_search.filter_tracks(loaded.search_records, needle)
            tracks = [loaded.tracks[index] for index, _record in matches]
        else:
            tracks = loaded.tracks
        total = len(tracks)
        page = tracks[offset:offset + limit]
        return {
            "playlist": dict(loaded.summary),
            "tracks": [dict(track) for track in page],
            "offset": offset,
            "limit": limit,
            "total": total,
            "hasMore": offset + len(page) < total,
        }

    def _load(self, playlist_id: str) -> _LoadedPlaylist:
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(playlist_id)
            if cached and now - cached.fetched_at < self.cache_ttl:
                self._cache.move_to_end(playlist_id)
                return cached
            key_lock = self._locks.setdefault(playlist_id, threading.Lock())
        with key_lock:
            with self._cache_lock:
                cached = self._cache.get(playlist_id)
                if cached and time.monotonic() - cached.fetched_at < self.cache_ttl:
                    self._cache.move_to_end(playlist_id)
                    return cached
            loaded = self._fetch(playlist_id)
            with self._cache_lock:
                self._cache[playlist_id] = loaded
                self._cache.move_to_end(playlist_id)
                while len(self._cache) > self.cache_items:
                    self._cache.popitem(last=False)
            return loaded

    def _fetch(self, playlist_id: str) -> _LoadedPlaylist:
        canonical = "https://music.youtube.com/playlist?list=" + playlist_id
        try:
            result = music.search(canonical)
        except Exception:
            raise UpstreamError(
                "This playlist is unavailable, private, or age-restricted. "
                "Only public and unlisted playlists are supported."
            ) from None
        if not isinstance(result, dict) or result.get("kind") != "playlist":
            raise UpstreamError(
                "YouTube Music did not return a playable public or unlisted playlist."
            )

        raw_tracks = result.get("tracks") or []
        tracks: list[dict[str, Any]] = []
        for item in raw_tracks:
            if not isinstance(item, dict):
                continue
            try:
                tracks.append(serialize_track(item))
            except (KeyError, TypeError, ValueError):
                continue
        if len(tracks) > self.max_tracks:
            raise UpstreamError(
                f"This playlist has more than {self.max_tracks:,} tracks, "
                "which this build cannot load in full."
            )

        metadata = result.get("playlist") if isinstance(result.get("playlist"), dict) else {}
        title = str(metadata.get("title") or result.get("title") or "YouTube Music playlist").strip()
        owner = str(metadata.get("owner") or "").strip()
        try:
            unavailable = max(0, int(result.get("unavailable") or 0))
        except (TypeError, ValueError):
            unavailable = 0
        summary = {
            "id": playlist_id,
            "title": (title or "YouTube Music playlist")[:200],
            "owner": owner[:120],
            "artworkUrl": _best_artwork(metadata.get("images")),
            "trackCount": len(tracks),
            "unavailableCount": unavailable,
        }
        return _LoadedPlaylist(
            summary=summary,
            tracks=tracks,
            search_records=[_search_record(track) for track in tracks],
            fetched_at=time.monotonic(),
        )
