"""Public YouTube Music search and M4A conversion for the mobile API.

The desktop ``music`` module already contains the public catalog search and
yt-dlp download logic, so this module reuses it instead of duplicating it.
The saved cookie file is read locally so broken sign-in can be named; this
module sends nothing itself and keeps upstream messages credential-free.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from functools import partial
import os
import requests
import unicodedata
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import music  # noqa: E402  (path setup must happen first)

VIDEO_ID = re.compile(r"^[\w-]{11}$", re.ASCII)

# The saved-cookie states reported by /v1/health. "configured" means only that
# the file holds sign-in rows that are not past their recorded expiry; YouTube
# can revoke a session server-side, and seeing that would take a network probe
# on a health poll, so the state never claims YouTube accepts the cookies.
SIGNIN_NOT_CONFIGURED = "not_configured"
SIGNIN_MISSING = "missing"
SIGNIN_INVALID = "invalid"
SIGNIN_NO_AUTH = "no_auth_cookies"
SIGNIN_EXPIRED = "expired"
SIGNIN_CONFIGURED = "configured"
SIGNIN_BROKEN = frozenset({SIGNIN_MISSING, SIGNIN_INVALID, SIGNIN_NO_AUTH, SIGNIN_EXPIRED})

# A signed-in YouTube export carries these; a file holding only preferences
# (PREF and the like) looks like cookies but cannot satisfy a sign-in gate.
AUTH_COOKIE_NAMES = frozenset({
    "SID", "HSID", "SSID", "APISID", "SAPISID", "LOGIN_INFO",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Host-1PLSID", "__Host-3PLSID",
})
COOKIE_DOMAINS = ("youtube.com", "google.com")

GENERIC_AUDIO_MESSAGE = (
    "YouTube could not provide this audio. It may be unavailable, "
    "region or age restricted, or require sign-in."
)

# A refusal caused by our own broken sign-in must not read as "this recording
# is unavailable": the user then retries tracks instead of fixing the cookies.
SIGNIN_BROKEN_MESSAGE = (
    "Ember's saved YouTube cookies are missing, unreadable or no longer "
    "signed in, so YouTube refused this recording. Export fresh cookies from "
    "the account and point EMBER_YOUTUBE_COOKIES at the new file."
)

# Both music.py refusal paths start with this phrase. Matching it keeps an
# unsupported-stream failure from being blamed on the cookies.
REFUSAL_MARKER = "could not provide this audio"


class UpstreamError(RuntimeError):
    """A fixed, credential-free message that is safe to return to the app."""


class SignInBrokenError(UpstreamError):
    """YouTube refused a recording because Ember's saved sign-in is unusable."""


def _cookie_rows(text: str):
    """Yield (domain, name, expiry) for each Netscape cookie row."""
    for line in text.splitlines():
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        elif line.startswith("#") or not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) < 7:
            continue
        try:
            expiry = int(fields[4])
        except ValueError:
            continue
        name = fields[5].strip()
        if name:
            yield fields[0].lstrip(".").lower(), name, expiry


def youtube_signin_state(path_value: str | None = None) -> str:
    """Classify the saved cookies from the file alone; never contact YouTube."""
    raw = os.environ.get("EMBER_YOUTUBE_COOKIES", "") if path_value is None else path_value
    configured = (raw or "").strip()
    if not configured:
        return SIGNIN_NOT_CONFIGURED
    try:
        with open(configured, "rb") as handle:
            data = handle.read(2 * 1024 * 1024)
    except OSError:
        return SIGNIN_MISSING
    if not data.strip():
        # A zero-byte file is not an empty cookie jar: yt-dlp refuses to load
        # it at all, so it cannot be treated as a harmless anonymous export.
        return SIGNIN_INVALID
    now = time.time()
    auth_seen = auth_live = False
    for domain, name, expiry in _cookie_rows(data.decode("utf-8", "replace")):
        if name not in AUTH_COOKIE_NAMES:
            continue
        if not any(domain == d or domain.endswith("." + d) for d in COOKIE_DOMAINS):
            continue
        auth_seen = True
        # Netscape expiry 0 means a session cookie, which is still live.
        if expiry == 0 or expiry > now:
            auth_live = True
    if not auth_seen:
        return SIGNIN_NO_AUTH
    return SIGNIN_CONFIGURED if auth_live else SIGNIN_EXPIRED


def refusal_is_signin(detail: str) -> bool:
    """Whether an upstream refusal is explained by our unusable sign-in.

    With a usable-looking cookie file the recording may simply be private, so
    it stays a generic refusal rather than telling the user to re-export.
    """
    return REFUSAL_MARKER in detail and youtube_signin_state() in SIGNIN_BROKEN


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

    def radio(self, recommendations: list[dict]) -> list[dict]:
        def canonical(value):
            return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if c.isalnum())

        def match(recommendation):
            try:
                with requests.Session() as session:
                    session.request = partial(session.request, timeout=5)
                    catalog = music.YTMusic(requests_session=session)
                    query = recommendation["title"] + " " + recommendation["artist"]
                    candidates = catalog.search(query, filter="songs", limit=5) or []
                for candidate in candidates[:5]:
                    if not isinstance(candidate, dict):
                        continue
                    try:
                        track = music.normalize(candidate)
                    except (KeyError, TypeError, ValueError):
                        continue
                    artists = [canonical(a["name"]) for a in track["artists"]]
                    if (canonical(track["name"]) == canonical(recommendation["title"])
                            and canonical(recommendation["artist"]) in artists
                            and candidate.get("isAvailable") is not False):
                        return serialize_track(track)
            except Exception:
                # One unavailable recording must not discard the rest of the mix.
                return None
            return None

        tracks, seen = [], set()
        with ThreadPoolExecutor(max_workers=4) as pool:
            for track in pool.map(match, recommendations[:12]):
                if track and track["id"] not in seen:
                    tracks.append(track)
                    seen.add(track["id"])
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
            _track, source = music.download_audio({"uri": uri}, work, max_bytes=self.max_audio_bytes)
        except Exception as error:
            if refusal_is_signin(str(error)):
                raise SignInBrokenError(SIGNIN_BROKEN_MESSAGE) from None
            raise UpstreamError(GENERIC_AUDIO_MESSAGE) from None
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
