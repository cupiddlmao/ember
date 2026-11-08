#!/usr/bin/env python3
"""Bounded JSON bridge to the checkout's existing YouTube music helpers.

Only public search and public stream resolution are exposed. The bridge never
reads or writes Ember's private login/configuration files and never prints
stream URLs or raw subprocess diagnostics.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import fcntl
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

MAX_INPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 512 * 1024
MAX_QUERY_CHARS = 200
MAX_TRACKS = 20
MAX_LYRIC_LINES = 800
MAX_LYRIC_TEXT_CHARS = 500
MAX_LYRIC_TIMESTAMP_MS = 24 * 60 * 60 * 1000
MAX_ARTWORK_URL_CHARS = 2048
ARTWORK_HOSTS = {
    "i.ytimg.com",
    "img.youtube.com",
    "lh3.googleusercontent.com",
    "lh4.googleusercontent.com",
    "lh5.googleusercontent.com",
    "lh6.googleusercontent.com",
    "yt3.ggpht.com",
    "yt3.googleusercontent.com",
}
VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
SECRET_PATTERN = re.compile(
    r"\b(token|signature|sig|key|auth|password|secret)=([^&\s]+)",
    re.IGNORECASE,
)


class RequestError(ValueError):
    """A safe, user-facing request error."""


class DependencyError(RuntimeError):
    """A missing local Python module, not an upstream music failure."""


def sanitize(value: object) -> str:
    text = str(value or "")
    text = URL_PATTERN.sub("[link removed]", text)
    text = SECRET_PATTERN.sub(r"\1=[redacted]", text)
    text = " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())
    return text[:240]


def emit(stream, payload: dict, limit=MAX_OUTPUT_BYTES) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > limit:
        encoded = json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "bridge",
                    "message": "The music bridge returned too much data.",
                },
            },
            separators=(",", ":"),
        )
    try:
        stream.write(encoded + "\n")
        stream.flush()
    except (BrokenPipeError, OSError):
        pass


def fail(stream, code: str, message: str) -> None:
    message = sanitize(message)
    if not message:
        message = "The music request could not be completed."
    emit(stream, {"ok": False, "error": {"code": code, "message": message}})


def read_request() -> dict:
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise RequestError("The music request was too large.")
    try:
        request = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError("The music request was not valid JSON.") from None
    if not isinstance(request, dict):
        raise RequestError("The music request must be an object.")
    return request


def text_value(value: object, label: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Invalid {label}.")
    value = value.strip()
    if not value or len(value) > limit or any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"Invalid {label}.")
    return value


def bounded_artwork_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if (
        not value
        or len(value) > MAX_ARTWORK_URL_CHARS
        or any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in value)
    ):
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or (parsed.hostname or "").lower() not in ARTWORK_HOSTS
    ):
        return None
    return value


def artwork_from(item: dict) -> str | None:
    groups = []
    album = item.get("album")
    if isinstance(album, dict):
        groups.append(album.get("images"))
    groups.append(item.get("thumbnails"))
    if item.get("thumbnail"):
        groups.append([item.get("thumbnail")])

    candidates: list[tuple[int, str]] = []
    for group in groups:
        if not isinstance(group, list):
            continue
        for entry in group:
            width = 0
            if isinstance(entry, dict):
                width = entry.get("width", 0)
                raw = entry.get("url")
            else:
                raw = entry
            url = bounded_artwork_url(raw)
            if url is None:
                continue
            try:
                width = int(width)
            except (TypeError, ValueError):
                width = 0
            if width < 0 or width > 1024:
                width = 0
            candidates.append((width, url))
    if not candidates:
        return None
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return candidates[0][1]


def slim_track(item: object) -> dict:
    if not isinstance(item, dict):
        raise ValueError("Invalid recording metadata.")
    video = item.get("id")
    if not isinstance(video, str) or not VIDEO_ID.fullmatch(video):
        raise ValueError("Invalid recording identifier.")
    expected_uri = f"https://music.youtube.com/watch?v={video}"
    if item.get("uri") != expected_uri:
        raise ValueError("Invalid recording link.")
    name = text_value(item.get("name"), "recording title", 300)
    raw_artists = item.get("artists")
    if not isinstance(raw_artists, list) or not raw_artists:
        raise ValueError("Invalid recording artist.")
    artists = []
    for artist in raw_artists[:8]:
        if isinstance(artist, dict):
            artist = artist.get("name")
        artists.append(text_value(artist, "recording artist", 200))
    album = item.get("album")
    if isinstance(album, dict):
        album = album.get("name")
    if album is None:
        album = ""
    if not isinstance(album, str) or len(album) > 300 or any(ord(ch) < 32 or ord(ch) == 127 for ch in album):
        raise ValueError("Invalid recording album.")
    duration = item.get("duration_ms", 0)
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        raise ValueError("Invalid recording duration.") from None
    if duration < 0 or duration > 86_400_000:
        raise ValueError("Invalid recording duration.")
    artwork = artwork_from(item)
    result = {
        "id": video,
        "uri": expected_uri,
        "name": name,
        "artists": artists,
        "album": album.strip(),
        "duration_ms": duration,
    }
    if artwork:
        result["artwork_url"] = artwork
    return result


def stream_url(value: object) -> str:
    if not isinstance(value, str) or len(value) > 4096:
        raise RuntimeError("YouTube returned an unsupported audio stream.")
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in value):
        raise RuntimeError("YouTube returned an unsupported audio stream.")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "googlevideo.com" or host.endswith(".googlevideo.com")):
        raise RuntimeError("YouTube returned an unsupported audio stream.")
    return value


def bounded_milliseconds(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 0 or value > MAX_LYRIC_TIMESTAMP_MS:
        return None
    return value


def lyric_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    text = "".join(character if character.isprintable() else " " for character in value)
    return " ".join(text.split())[:MAX_LYRIC_TEXT_CHARS]


def normalize_lyrics_payload(payload: object, track_id: str) -> dict:
    """Bound and validate the LRCLIB service's line data.

    Timestamps are integer milliseconds. Booleans, floats, non-finite values,
    negative times, reversed intervals and oversized lines are rejected rather
    than estimated. Plain lyrics deliberately carry no seekable timestamps.
    """
    if not isinstance(payload, dict):
        raise RuntimeError("The lyrics service returned invalid data.")
    provider_kind = payload.get("kind")
    if provider_kind not in {"synced", "plain", "instrumental", "none"}:
        raise RuntimeError("The lyrics service returned an invalid state.")
    source = payload.get("source")
    if source not in (None, "lrclib"):
        raise RuntimeError("The lyrics service returned an unsupported source.")

    kind = "missing" if provider_kind == "none" else provider_kind
    raw_lines = payload.get("lines") or []
    if not isinstance(raw_lines, list):
        raise RuntimeError("The lyrics service returned invalid lyric lines.")
    raw_lines = raw_lines[:MAX_LYRIC_LINES]
    lines = []
    message = None

    if provider_kind == "synced":
        for item in raw_lines:
            if not isinstance(item, dict):
                continue
            start = bounded_milliseconds(item.get("startMs"))
            if start is None:
                continue
            end = bounded_milliseconds(item.get("endMs"))
            if end is not None and end < start:
                continue
            text = lyric_text(item.get("text"))
            if text:
                lines.append({"text": text, "start_ms": start, "end_ms": end})
        lines.sort(key=lambda line: line["start_ms"])
        if not lines:
            kind = "missing"
            message = "LRCLIB returned no usable synchronized lyrics for this recording."
    elif provider_kind == "plain":
        for item in raw_lines:
            text = lyric_text(item.get("text") if isinstance(item, dict) else item)
            if text:
                lines.append({"text": text, "start_ms": None, "end_ms": None})
        if not lines:
            kind = "missing"
            message = "LRCLIB returned no usable plain lyrics for this recording."
        else:
            message = "These lyrics have no synchronized timestamps and are not seekable."
    else:
        lines = []
        if kind == "instrumental":
            message = "LRCLIB marks this recording as instrumental."
        elif not message:
            message = "LRCLIB has no lyrics for this recording."

    if provider_kind == "synced" and lines and not message:
        message = None
    provider_message = lyric_text(payload.get("message"))
    if provider_message:
        message = provider_message
    if message:
        message = sanitize(message)
    return {
        "track_id": track_id,
        "source": "lrclib",
        "kind": kind,
        "lines": lines[:MAX_LYRIC_LINES],
        "message": message or None,
    }


def lyrics_for(service: object, track: dict) -> dict:
    album = track.get("album")
    if isinstance(album, dict):
        album = album.get("name", "")
    payload = service.lookup(
        track["id"],
        track["name"],
        track["artists"][0]["name"],
        album if isinstance(album, str) else "",
        track["duration_ms"],
    )
    return normalize_lyrics_payload(payload, track["id"])


def request_track(value: object) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Select a valid recording.")
    video = value.get("id")
    if not isinstance(video, str) or not VIDEO_ID.fullmatch(video):
        raise ValueError("Select a valid recording.")
    uri = f"https://music.youtube.com/watch?v={video}"
    if value.get("uri") != uri:
        raise ValueError("Select a valid recording.")
    name = value.get("name")
    artists = value.get("artists")
    album = value.get("album", "")
    duration = value.get("duration_ms", 0)
    if not isinstance(name, str) or not name.strip():
        name = "Unknown recording"
    if not isinstance(artists, list):
        artists = []
    artist_dicts = [
        {"name": str(artist)[:200]}
        for artist in artists[:8]
        if isinstance(artist, str) and artist.strip()
    ]
    if not artist_dicts:
        artist_dicts = [{"name": "Unknown artist"}]
    if not isinstance(album, str):
        album = ""
    if not isinstance(duration, int) or duration < 0:
        duration = 0
    artwork = bounded_artwork_url(value.get("artwork_url"))
    result = {
        "id": video,
        "uri": uri,
        "name": name.strip()[:300],
        "artists": artist_dicts,
        "album": {"name": album.strip()[:300]},
        "duration_ms": duration,
    }
    if artwork:
        result["artwork_url"] = artwork
    return result


def cache_root() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ember-player" / "rust-audio"


def cache_store():
    from audio_cache import AudioStore
    root = cache_root()
    return AudioStore(cache_root=root / "audio", download_root=root / "downloads",
                      index_path=root / "index.sqlite3")


def cache_enabled() -> bool:
    try:
        value = json.loads((cache_root() / "settings.json").read_text())
        return value.get("enabled") is True
    except FileNotFoundError:
        return True
    except (OSError, ValueError, AttributeError):
        return False


def cached_audio(track):
    try:
        item = cache_store().lookup(track)
        if item:
            path = item["path"].resolve()
            if path.is_relative_to((cache_root() / "audio").resolve()):
                return path.as_uri()
        return None
    except Exception:
        return None  # A broken/unwritable cache must never prevent streaming.


def cache_action(action, track=None):
    store = cache_store()
    root = cache_root()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # One writer across app instances; cancelled downloads are reclaimed under this lock.
    with (root / "writer.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        store.cleanup()
        indexed = {item["path"].resolve() for item in store.entries().values()}
        for path in store.cache_root.glob("*/*"):
            if path.is_file() and path.resolve() not in indexed:
                path.unlink(missing_ok=True)
        if action == "clear_cache":
            store.clear_auto()
        elif cache_enabled():
            store.download(track)


def run(real_stdout) -> None:
    request = read_request()
    action = request.get("action")
    if action not in {"search", "resolve", "lyrics", "cache", "clear_cache", "library"}:
        raise RequestError("Unknown music bridge action.")

    sys.path.insert(0, str(ROOT))
    if action == "library":
        from library_client import execute
        command = request.get("command")
        if not isinstance(command, dict):
            raise RequestError("Invalid library command.")
        with contextlib.redirect_stdout(sys.stderr):
            state = execute(command)
        emit(real_stdout, {"ok": True, "data": state}, limit=10 * 1024 * 1024)
        return
    if action == "lyrics":
        track = request_track(request.get("track"))
        try:
            from mobile.backend.lyrics_service import LyricsError, LyricsService  # type: ignore
        except ImportError:
            raise DependencyError(
                "The checkout lyrics module is unavailable. Check the mobile backend files."
            ) from None
        try:
            payload = lyrics_for(LyricsService(timeout=12.0), track)
        except LyricsError as error:
            raise RuntimeError(str(error)) from None
        with contextlib.redirect_stdout(sys.stderr):
            emit(real_stdout, {"ok": True, "data": payload})
        return

    # Library imports can be noisy; keep stdout exclusively for the protocol.
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import music  # type: ignore
    except ImportError:
        raise DependencyError(
            "The checkout Python environment is missing music dependencies. "
            "Run the checkout setup and try again."
        ) from None

    if action in {"cache", "clear_cache"}:
        track = request_track(request.get("track")) if action == "cache" else None
        with contextlib.redirect_stdout(sys.stderr):
            cache_action(action, track)
        emit(real_stdout, {"ok": True, "data": {"cleared": action == "clear_cache"}})
        return

    if action == "search":
        query = request.get("query")
        if not isinstance(query, str):
            raise RequestError("Enter a song or artist.")
        query = query.strip()
        if not query or len(query) > MAX_QUERY_CHARS or any(ord(ch) < 32 or ord(ch) == 127 for ch in query):
            raise RequestError("Enter a song or artist (200 characters max).")
        with contextlib.redirect_stdout(sys.stderr):
            result = music.search(query)
        if not isinstance(result, dict) or not isinstance(result.get("tracks"), list):
            raise RuntimeError("YouTube Music returned an invalid search result.")
        tracks = []
        seen = set()
        for item in result["tracks"]:
            if len(tracks) >= MAX_TRACKS:
                break
            try:
                track = slim_track(item)
            except ValueError:
                continue
            if track["id"] not in seen:
                seen.add(track["id"])
                tracks.append(track)
        emit(real_stdout, {"ok": True, "data": {"tracks": tracks}})
        return

    track = request_track(request.get("track"))
    with contextlib.redirect_stdout(sys.stderr):
        local = cached_audio(track)
    if local:
        emit(real_stdout, {"ok": True, "data": {"track": slim_track(track), "stream_url": local}})
        return
    with contextlib.redirect_stdout(sys.stderr):
        resolved_track, url = music.resolve(track)
    if not isinstance(resolved_track, dict):
        raise RuntimeError("YouTube returned invalid recording metadata.")
    slimmed = slim_track(resolved_track)
    if not slimmed.get("artwork_url") and track.get("artwork_url"):
        slimmed["artwork_url"] = track["artwork_url"]
    emit(
        real_stdout,
        {
            "ok": True,
            "data": {
                "track": slimmed,
                "stream_url": stream_url(url),
            },
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--root", required=True)
    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    root = Path(args.root).expanduser()
    try:
        root = root.resolve(strict=True)
    except OSError:
        print("Checkout path is unavailable.", file=sys.stderr)
        return 2
    if not (root / "music.py").is_file():
        print("Checkout does not contain music.py.", file=sys.stderr)
        return 2

    global ROOT
    ROOT = root
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        run(real_stdout)
    except RequestError as error:
        fail(real_stdout, "input", str(error))
    except DependencyError as error:
        fail(real_stdout, "dependencies", str(error))
    except ValueError as error:
        fail(real_stdout, "input", str(error))
    except RuntimeError as error:
        fail(real_stdout, "unavailable", str(error))
    except Exception:
        fail(
            real_stdout,
            "internal",
            "YouTube Music could not complete the request. Check the connection and try again.",
        )
    return 0


ROOT = Path(".")
if __name__ == "__main__":
    raise SystemExit(main())
