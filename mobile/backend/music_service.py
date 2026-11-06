"""Public YouTube Music search and M4A conversion for the mobile API.

The desktop ``music`` module already contains the public catalog search and
yt-dlp download logic, so this module reuses it instead of duplicating it.
No Google account credentials are read or used.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import music  # noqa: E402  (path setup must happen first)

VIDEO_ID = re.compile(r"^[\w-]{11}$", re.ASCII)


class UpstreamError(RuntimeError):
    """A fixed, credential-free message that is safe to return to the app."""


def _artist(track: dict[str, Any]) -> str:
    names = []
    for artist in track.get("artists") or []:
        if isinstance(artist, dict) and artist.get("name"):
            names.append(str(artist["name"]))
    return ", ".join(names) or "Unknown artist"


def _artwork(track: dict[str, Any]) -> str | None:
    album = track.get("album")
    images = album.get("images") if isinstance(album, dict) else None
    best: tuple[int, str] | None = None
    for image in images or []:
        if not isinstance(image, dict):
            continue
        url = image.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            continue
        area = int(image.get("width") or 0) * int(image.get("height") or 0)
        if best is None or area >= best[0]:
            best = (area, url)
    return best[1] if best else None


def serialize_track(track: dict[str, Any]) -> dict[str, Any]:
    album = track.get("album")
    return {
        "id": str(track["id"]),
        "title": str(track.get("name") or "Untitled"),
        "artist": _artist(track),
        "album": str(album.get("name") or "") if isinstance(album, dict) else "",
        "durationMs": int(track.get("duration_ms") or 0),
        "artworkUrl": _artwork(track),
    }


class MusicService:
    def __init__(self, cache, max_audio_bytes: int):
        self.cache = cache
        self.max_audio_bytes = max_audio_bytes

    def search(self, query: str, limit: int) -> list[dict[str, Any]]:
        try:
            result = music.search(query)
        except Exception:
            raise UpstreamError(
                "YouTube Music search is unavailable right now. Try again in a moment."
            ) from None
        if not isinstance(result, dict):
            raise UpstreamError("YouTube Music returned an unexpected search result.")
        tracks: list[dict[str, Any]] = []
        for item in result.get("tracks") or []:
            if not isinstance(item, dict):
                continue
            try:
                serialized = serialize_track(item)
            except (KeyError, TypeError, ValueError):
                continue
            if VIDEO_ID.fullmatch(serialized["id"]) and serialized not in tracks:
                tracks.append(serialized)
            if len(tracks) >= limit:
                break
        return tracks

    def audio_file(self, video_id: str) -> Path:
        if not VIDEO_ID.fullmatch(video_id):
            raise ValueError("Invalid track ID.")
        return self.cache.get_or_create(
            video_id, lambda work: self._create_audio(video_id, work)
        )

    def _create_audio(self, video_id: str, work: Path) -> Path:
        uri = f"https://music.youtube.com/watch?v={video_id}"
        try:
            _track, source = music.download_audio({"uri": uri}, work)
        except Exception:
            raise UpstreamError(
                "YouTube could not provide this audio. It may be unavailable, "
                "region or age restricted, or require sign-in."
            ) from None
        destination = work / "prepared.m4a"
        _convert_to_m4a(Path(source), destination)
        if destination.stat().st_size > self.max_audio_bytes:
            raise UpstreamError("This track is larger than the server's audio limit.")
        return destination


def _convert_to_m4a(source: Path, destination: Path) -> None:
    """Transcode any yt-dlp audio output to AAC in an MP4 container.

    AVPlayer on iPhone reliably plays AAC/M4A. WebM/Opus from YouTube is not a
    format the app assumes, so the server normalizes it here.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise UpstreamError("The server is missing FFmpeg, so audio cannot be prepared.")
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-vn",
        "-map",
        "0:a:0",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(destination),
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            timeout=180,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise UpstreamError("The server could not prepare this audio in time.") from None
    if result.returncode != 0:
        raise UpstreamError("The server could not convert this audio. Try another recording.")
    try:
        size = destination.stat().st_size
    except OSError:
        size = 0
    if size <= 0:
        raise UpstreamError("The server produced an empty audio file.")
