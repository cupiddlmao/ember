"""Authenticated LRCLIB lyrics lookup and conservative timing normalization.

LRCLIB is a free, no-key API. It asks clients to identify themselves with a
User-Agent. Word timings are accepted only when LRCLIB explicitly supplies them
in its Lyricsfile YAML; this module never invents word timestamps.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

LRCLIB_GET = "https://lrclib.net/api/get"
USER_AGENT = "EmberMobile/0.2.0 (https://github.com/cupiddlmao/ember)"
MAX_RESPONSE_BYTES = 1_000_000
MAX_LYRICS_FILE_BYTES = 512_000
MAX_LINES = 5000
MAX_WORDS_PER_LINE = 500
MAX_TIMESTAMP_MS = 24 * 60 * 60 * 1000
TRACK_ID = re.compile(r"^[\w-]{11}$", re.ASCII)
LRC_TIMESTAMP = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
LRC_OFFSET = re.compile(r"\[offset:([+-]?\d+)\]", re.IGNORECASE)


class LyricsError(RuntimeError):
    """A fixed message that is safe to show in the app."""


def _milliseconds(minutes: str, seconds: str, fraction: str | None) -> int:
    minutes_value = int(minutes)
    seconds_value = int(seconds)
    if seconds_value >= 60:
        raise ValueError("Invalid LRC seconds")
    fractional = (fraction or "0").ljust(3, "0")[:3]
    return ((minutes_value * 60) + seconds_value) * 1000 + int(fractional)


def _line(text: str, start: int | None, end: int | None, words: list[dict] | None = None) -> dict:
    value = {
        "text": text.strip(),
        "startMs": start,
        "endMs": end,
        "words": words or [],
    }
    if value["text"]:
        return value
    return {}


def parse_lrc(text: str) -> list[dict]:
    """Parse line-synced LRC. Word tags are ignored, not inferred."""
    if not isinstance(text, str) or len(text) > MAX_LYRICS_FILE_BYTES:
        return []
    offset = LRC_OFFSET.search(text)
    offset_ms = int(offset.group(1)) if offset else 0
    rows: list[tuple[int, str]] = []
    for raw in text.splitlines():
        stamps = list(LRC_TIMESTAMP.finditer(raw))
        if not stamps:
            continue
        content = LRC_TIMESTAMP.sub("", raw)
        content = re.sub(r"<[^>]*>", "", content).strip()
        if not content:
            continue
        for stamp in stamps:
            try:
                start = _milliseconds(stamp.group(1), stamp.group(2), stamp.group(3))
            except (TypeError, ValueError):
                continue
            start = max(0, start + offset_ms)
            if start <= MAX_TIMESTAMP_MS:
                rows.append((start, content))
        if len(rows) > MAX_LINES:
            rows = rows[:MAX_LINES]
            break
    rows.sort(key=lambda item: item[0])
    result = []
    for index, (start, content) in enumerate(rows):
        end = rows[index + 1][0] if index + 1 < len(rows) else None
        line = _line(content, start, end)
        if line:
            result.append(line)
    return result


def parse_lyricsfile(text: str) -> list[dict]:
    """Parse the explicit start/end timings in LRCLIB's Lyricsfile YAML.

    PyYAML runs with aliases disabled, then the small fixed schema is validated.
    Missing word ends stay missing; they are never estimated from line timing.
    """
    if not isinstance(text, str) or not text or len(text) > MAX_LYRICS_FILE_BYTES:
        return []
    try:
        import yaml
        from yaml.events import AliasEvent

        class NoAliasLoader(yaml.SafeLoader):
            def compose_node(self, parent, index):
                if self.check_event(AliasEvent):
                    raise yaml.YAMLError("YAML aliases are not accepted")
                return super().compose_node(parent, index)

        document = yaml.load(text, Loader=NoAliasLoader)
    except Exception:
        return []
    if not isinstance(document, dict):
        return []
    raw_lines = document.get("lines")
    if not isinstance(raw_lines, list) or len(raw_lines) > MAX_LINES:
        return []

    def bounded_ms(value) -> int | None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        value = int(value)
        if value < 0 or value > MAX_TIMESTAMP_MS:
            return None
        return value

    lines: list[dict] = []
    for item in raw_lines:
        if not isinstance(item, dict):
            continue
        start = bounded_ms(item.get("start_ms"))
        if start is None:
            continue
        end = bounded_ms(item.get("end_ms"))
        if end is not None and end < start:
            end = None
        raw_words = item.get("words")
        words: list[dict] = []
        if isinstance(raw_words, list) and len(raw_words) <= MAX_WORDS_PER_LINE:
            for raw_word in raw_words:
                if not isinstance(raw_word, dict):
                    words = []
                    break
                text_value = raw_word.get("text")
                word_start = bounded_ms(raw_word.get("start_ms"))
                if not isinstance(text_value, str) or word_start is None:
                    words = []
                    break
                word_end = bounded_ms(raw_word.get("end_ms"))
                if word_end is not None and word_end < word_start:
                    word_end = None
                if text_value:
                    words.append({
                        "text": text_value[:2000],
                        "startMs": word_start,
                        "endMs": word_end,
                    })
        complete = item.get("text")
        if not isinstance(complete, str) or not complete.strip():
            complete = "".join(word["text"] for word in words)
        value = _line(complete[:5000], start, end, words)
        if value:
            lines.append(value)
    lines.sort(key=lambda item: item["startMs"])
    return lines[:MAX_LINES]


def plain_lines(text: str) -> list[dict]:
    if not isinstance(text, str):
        return []
    result = []
    for row in text.splitlines()[:MAX_LINES]:
        value = _line(row, None, None)
        if value:
            result.append(value)
    return result


class LyricsService:
    def __init__(self, timeout: float = 12.0, opener=None):
        self.timeout = max(1.0, float(timeout))
        self._opener = opener or urllib.request.urlopen

    def lookup(
        self,
        track_id: str,
        title: str,
        artist: str,
        album: str = "",
        duration_ms: int = 0,
    ) -> dict[str, Any]:
        if not TRACK_ID.fullmatch(str(track_id or "")):
            raise ValueError("Invalid track ID.")
        title = " ".join(str(title or "").split())[:200]
        artist = " ".join(str(artist or "").split())[:200]
        album = " ".join(str(album or "").split())[:200]
        if not title or not artist:
            raise ValueError("Track title and artist are required for lyrics.")
        try:
            duration_ms = int(duration_ms or 0)
        except (TypeError, ValueError):
            duration_ms = 0
        duration_ms = max(0, min(4 * 60 * 60 * 1000, duration_ms))

        params = {"track_name": title, "artist_name": artist}
        if album:
            params["album_name"] = album
        if duration_ms >= 1000:
            params["duration"] = str(min(3600, max(1, round(duration_ms / 1000))))
        payload = self._request(params)
        if payload is None:
            return {
                "trackId": track_id,
                "source": "lrclib",
                "kind": "none",
                "lines": [],
                "message": "LRCLIB has no lyrics for this recording.",
            }

        if payload.get("instrumental") is True:
            return {
                "trackId": track_id,
                "source": "lrclib",
                "kind": "instrumental",
                "lines": [],
                "message": "LRCLIB marks this recording as instrumental.",
            }

        lines: list[dict] = []
        if payload.get("hasWordSync") is True and isinstance(payload.get("lyricsfile"), str):
            lines = parse_lyricsfile(payload["lyricsfile"])
        if not lines and isinstance(payload.get("syncedLyrics"), str):
            lines = parse_lrc(payload["syncedLyrics"])
        if lines:
            return {
                "trackId": track_id,
                "source": "lrclib",
                "kind": "synced",
                "lines": lines,
            }
        plain = plain_lines(payload.get("plainLyrics"))
        if plain:
            return {
                "trackId": track_id,
                "source": "lrclib",
                "kind": "plain",
                "lines": plain,
                "message": "No synchronized timestamps are available for this recording.",
            }
        return {
            "trackId": track_id,
            "source": "lrclib",
            "kind": "none",
            "lines": [],
            "message": "LRCLIB has no usable lyrics for this recording.",
        }

    def _request(self, params: dict[str, str]) -> dict[str, Any] | None:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            LRCLIB_GET + "?" + query,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with self._opener(request, timeout=self.timeout) as response:
                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type.lower():
                    raise LyricsError("The lyrics service returned an unexpected response.")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            if error.code == 429:
                raise LyricsError("The lyrics service is rate limited. Try again shortly.") from None
            if error.code == 503:
                raise LyricsError("The lyrics service is busy. Try again shortly.") from None
            raise LyricsError("The lyrics service could not answer right now.") from None
        except LyricsError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise LyricsError("Could not reach the lyrics service.") from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LyricsError("The lyrics response was too large.")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LyricsError("The lyrics service returned invalid data.") from None
        if not isinstance(payload, dict):
            raise LyricsError("The lyrics service returned invalid data.")
        return payload
