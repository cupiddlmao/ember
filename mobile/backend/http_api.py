"""Bounded, authenticated HTTP API for the Ember iPhone app."""
from __future__ import annotations

import hmac
import json
import logging
import re
import shutil
import sqlite3
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .audio_cache import AudioCache
from .config import Settings
from .library_store import LibraryError, LibraryStore
from .lyrics_service import LyricsError, LyricsService
from .music_service import MusicService, UpstreamError
from .playlist_service import PlaylistInputError, PlaylistService

VERSION = "0.3.0"
DEFAULT_SEARCH_LIMIT = 20
MAX_QUERY_CHARS = 200
MAX_RAW_QUERY_CHARS = 512
MAX_PLAYLIST_RAW_QUERY_CHARS = 8192
AUDIO_ROUTE = re.compile(r"^/v1/tracks/([\w-]{11})/audio$", re.ASCII)
PLAYLIST_ROUTE = re.compile(r"^/v1/playlists/([\w-]{10,200})$", re.ASCII)
LYRICS_ROUTE = re.compile(r"^/v1/tracks/([\w-]{11})/lyrics$", re.ASCII)

LIBRARY_ROOT = "/v1/library/playlists"
LIBRARY_ROUTE = re.compile(r"^/v1/library/playlists/([a-f0-9-]{36})$", re.ASCII)
MAX_LIBRARY_BODY = 8 * 1024 * 1024

logger = logging.getLogger("ember.api")
logger.addHandler(logging.NullHandler())


def _reject_json_constant(_value):
    raise ValueError("Non-finite JSON number")


class RateLimiter:
    """A small per-client sliding-window limiter for one-user deployments."""

    def __init__(self, limit: int, window: float = 60.0):
        self.limit = max(1, int(limit))
        self.window = float(window)
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(key, deque())
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            if len(self._events) > 512:
                for stale_key, stale in list(self._events.items()):
                    if not stale or now - stale[-1] >= self.window:
                        self._events.pop(stale_key, None)
            return True


class EmberServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address,
        settings: Settings,
        service,
        limiter: RateLimiter | None = None,
        playlists=None,
        lyrics=None,
        library=None,
    ):
        self.settings = settings
        self.service = service
        self.playlists = playlists or PlaylistService()
        self.lyrics = lyrics or LyricsService()
        self.library = library if library is not None else LibraryStore(settings.library_dir)
        self.limiter = limiter or RateLimiter(settings.rate_limit_per_minute)
        self.jobs = threading.BoundedSemaphore(max(1, settings.max_concurrent_jobs))
        super().__init__(address, ApiHandler)


class ApiHandler(BaseHTTPRequestHandler):
    server: EmberServer
    protocol_version = "HTTP/1.1"
    server_version = "EmberMobileAPI/" + VERSION

    # The default logger writes full request paths. Never log query strings,
    # headers, access keys or upstream URLs.
    def log_message(self, _format, *_args):
        return

    def log_error(self, _format, *_args):
        return

    def version_string(self):
        return self.server_version

    def do_GET(self):
        # Reject/close unread bodies on auth and framing errors; never parse them as another request.
        if self.command != "GET":
            self.close_connection = True
        parsed = urlsplit(self.path)
        started = time.monotonic()
        status = 500
        try:
            status = self._route(parsed)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            status = 499
        except Exception as error:  # never return exception details to clients
            logger.error("Unhandled request failure (%s)", type(error).__name__)
            try:
                self._send_error(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    "internal_error",
                    "The server hit an unexpected problem.",
                )
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            status = 500
        finally:
            logger.info(
                "%s %s %d %dms",
                self.command,
                parsed.path,
                status,
                round((time.monotonic() - started) * 1000),
            )

    do_POST = do_GET
    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET

    @staticmethod
    def _route_path(path: str) -> str:
        """Accept the public /ember base path with or without a stripping proxy."""
        if path == "/ember":
            return "/"
        if path.startswith("/ember/"):
            return path[len("/ember"):]
        return path

    def _route(self, parsed) -> int:
        if not self.server.limiter.allow(self.client_address[0]):
            self._send_error(
                HTTPStatus.TOO_MANY_REQUESTS,
                "rate_limited",
                "Too many requests. Wait a moment and try again.",
                {"Retry-After": "30"},
            )
            return HTTPStatus.TOO_MANY_REQUESTS

        if not self._authorized():
            self._send_error(
                HTTPStatus.UNAUTHORIZED,
                "unauthorized",
                "A valid access key is required.",
                {"WWW-Authenticate": 'Bearer realm="ember"'},
            )
            return HTTPStatus.UNAUTHORIZED

        route_path = self._route_path(parsed.path)
        library_match = LIBRARY_ROUTE.fullmatch(route_path)
        if route_path == LIBRARY_ROOT or library_match:
            return self._library(library_match.group(1) if library_match else None)
        if self.command != "GET":
            self._send_error(405, "method_not_allowed", "Only GET is supported for this route.", {"Allow": "GET"})
            return 405

        if route_path == "/v1/health":
            self._send_json(HTTPStatus.OK, {"status": "ok", "version": VERSION})
            return HTTPStatus.OK

        if route_path == "/v1/search":
            return self._search(parsed)

        if route_path == "/v1/playlists/preview":
            return self._playlist_preview(parsed)

        playlist = PLAYLIST_ROUTE.fullmatch(route_path)
        if playlist:
            return self._playlist_page(playlist.group(1), parsed)

        audio = AUDIO_ROUTE.fullmatch(route_path)
        if audio:
            return self._audio(audio.group(1))

        lyrics = LYRICS_ROUTE.fullmatch(route_path)
        if lyrics:
            return self._track_lyrics(lyrics.group(1), parsed)

        self._send_error(HTTPStatus.NOT_FOUND, "not_found", "Unknown API route.")
        return HTTPStatus.NOT_FOUND

    def _library_body(self):
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") is not None or len(lengths) != 1:
            raise LibraryError(400, "invalid_body", "Exactly one Content-Length is required; chunked bodies are unsupported.")
        try:
            if not re.fullmatch(r"[0-9]+", lengths[0]):
                raise ValueError
            size = int(lengths[0])
        except ValueError:
            raise LibraryError(400, "invalid_body", "Invalid Content-Length.") from None
        if not 0 < size <= MAX_LIBRARY_BODY:
            raise LibraryError(413, "body_too_large", "Playlist requests must be between 1 byte and 8 MiB.")
        if self.headers.get_content_type() != "application/json":
            raise LibraryError(415, "invalid_body", "Use application/json.")
        deadline = time.monotonic() + 10
        previous_timeout = self.connection.gettimeout()
        body = bytearray()
        try:
            while len(body) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                self.connection.settimeout(remaining)
                chunk = self.rfile.read1(min(65536, size - len(body)))
                if not chunk:
                    raise ValueError
                body.extend(chunk)
            return json.loads(body.decode("utf-8"), parse_constant=_reject_json_constant)
        except (OSError, ValueError, RecursionError):
            raise LibraryError(400, "invalid_body", "Incomplete, invalid or timed-out JSON body.") from None
        finally:
            self.connection.settimeout(previous_timeout)

    def _library(self, playlist_id):
        allowed = "GET, PUT, DELETE" if playlist_id else "GET, POST"
        if self.command not in allowed.split(", "):
            self._send_error(405, "method_not_allowed", "Unsupported library method.", {"Allow": allowed})
            return 405
        try:
            if self.command == "GET":
                data = self.server.library.get(playlist_id) if playlist_id else self.server.library.list()
                status = 200
            else:
                status, data = self.server.library.mutate(self.command, playlist_id, self._library_body())
        except LibraryError as error:
            self._send_error(error.status, error.code, str(error))
            return error.status
        except sqlite3.OperationalError:
            self._send_error(503, "library_unavailable", "Playlist storage is busy or unavailable. Retry the same request.")
            return 503
        self._send_json(status, data)
        return status

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        scheme, separator, supplied = header.partition(" ")
        if not separator or scheme.lower() != "bearer":
            return False
        supplied = supplied.strip()
        if not supplied:
            return False
        return hmac.compare_digest(supplied.encode("utf-8"), self.server.settings.token.encode("utf-8"))

    def _begin_job(self) -> bool:
        acquired = self.server.jobs.acquire(timeout=5)
        if not acquired:
            self._send_error(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "busy",
                "The server is busy preparing music. Try again in a moment.",
                {"Retry-After": "5"},
            )
        return acquired

    def _search(self, parsed) -> int:
        if len(parsed.query) > MAX_RAW_QUERY_CHARS:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_query", "The search request is too large.")
            return HTTPStatus.BAD_REQUEST
        values = parse_qs(parsed.query, keep_blank_values=True)
        query = (values.get("q") or [""])[0].strip()
        if not query:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_query", "Enter a search term.")
            return HTTPStatus.BAD_REQUEST
        if len(query) > MAX_QUERY_CHARS:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_query", "Search terms are limited to 200 characters.")
            return HTTPStatus.BAD_REQUEST

        raw_limit = (values.get("limit") or [str(DEFAULT_SEARCH_LIMIT)])[0]
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_limit", "The limit must be a whole number.")
            return HTTPStatus.BAD_REQUEST
        limit = max(1, min(self.server.settings.max_search_limit, limit))

        if not self._begin_job():
            return HTTPStatus.SERVICE_UNAVAILABLE
        try:
            tracks = self.server.service.search(query, limit)
        except UpstreamError as error:
            self._send_error(HTTPStatus.BAD_GATEWAY, "upstream_error", str(error))
            return HTTPStatus.BAD_GATEWAY
        except Exception as error:
            logger.error("Search failed (%s)", type(error).__name__)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The server could not search right now.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            self.server.jobs.release()

        self._send_json(HTTPStatus.OK, {"tracks": tracks})
        return HTTPStatus.OK

    def _audio(self, video_id: str) -> int:
        if not self._begin_job():
            return HTTPStatus.SERVICE_UNAVAILABLE
        try:
            path = self.server.service.audio_file(video_id)
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_track", "Invalid track ID.")
            return HTTPStatus.BAD_REQUEST
        except UpstreamError as error:
            self._send_error(HTTPStatus.BAD_GATEWAY, "upstream_error", str(error))
            return HTTPStatus.BAD_GATEWAY
        except Exception as error:
            logger.error("Audio preparation failed (%s)", type(error).__name__)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The server could not prepare this audio.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            self.server.jobs.release()

        try:
            handle = Path(path).open("rb")
        except OSError:
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The prepared audio is unavailable.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR

        with handle:
            size = handle.seek(0, 2)
            handle.seek(0)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "audio/mp4")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition", f'attachment; filename="{video_id}.m4a"')
            self.send_header("Cache-Control", "private, max-age=86400")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            shutil.copyfileobj(handle, self.wfile, length=64 * 1024)
        return HTTPStatus.OK

    def _playlist_preview(self, parsed) -> int:
        if len(parsed.query) > MAX_PLAYLIST_RAW_QUERY_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_playlist",
                "The playlist request is too large.",
            )
            return HTTPStatus.BAD_REQUEST
        values = parse_qs(parsed.query, keep_blank_values=True)
        playlist_url = (values.get("url") or [""])[0].strip()
        raw_limit = (values.get("limit") or ["50"])[0]
        try:
            limit = max(1, min(100, int(raw_limit)))
        except (TypeError, ValueError):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_limit",
                "The playlist page size must be a whole number.",
            )
            return HTTPStatus.BAD_REQUEST

        if not self._begin_job():
            return HTTPStatus.SERVICE_UNAVAILABLE
        try:
            page = self.server.playlists.preview(playlist_url, limit)
        except PlaylistInputError as error:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_playlist", str(error))
            return HTTPStatus.BAD_REQUEST
        except UpstreamError as error:
            self._send_error(HTTPStatus.BAD_GATEWAY, "upstream_error", str(error))
            return HTTPStatus.BAD_GATEWAY
        except Exception as error:
            logger.error("Playlist preview failed (%s)", type(error).__name__)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The server could not load that playlist.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            self.server.jobs.release()

        self._send_json(HTTPStatus.OK, page)
        return HTTPStatus.OK

    def _playlist_page(self, playlist_id: str, parsed) -> int:
        if len(parsed.query) > MAX_PLAYLIST_RAW_QUERY_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_playlist",
                "The playlist request is too large.",
            )
            return HTTPStatus.BAD_REQUEST
        values = parse_qs(parsed.query, keep_blank_values=True)
        raw_offset = (values.get("offset") or ["0"])[0]
        raw_limit = (values.get("limit") or ["50"])[0]
        query = (values.get("q") or [""])[0].strip()
        try:
            offset = int(raw_offset)
            limit = int(raw_limit)
        except (TypeError, ValueError):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_pagination",
                "Playlist pagination values must be whole numbers.",
            )
            return HTTPStatus.BAD_REQUEST
        if offset < 0 or limit < 1:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_pagination",
                "Playlist pagination values are out of range.",
            )
            return HTTPStatus.BAD_REQUEST
        if len(query) > MAX_QUERY_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_query",
                "Playlist searches are limited to 200 characters.",
            )
            return HTTPStatus.BAD_REQUEST

        if not self._begin_job():
            return HTTPStatus.SERVICE_UNAVAILABLE
        try:
            page = self.server.playlists.page(playlist_id, offset, limit, query)
        except PlaylistInputError as error:
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid_playlist", str(error))
            return HTTPStatus.BAD_REQUEST
        except UpstreamError as error:
            self._send_error(HTTPStatus.BAD_GATEWAY, "upstream_error", str(error))
            return HTTPStatus.BAD_GATEWAY
        except Exception as error:
            logger.error("Playlist page failed (%s)", type(error).__name__)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The server could not load that playlist.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            self.server.jobs.release()

        self._send_json(HTTPStatus.OK, page)
        return HTTPStatus.OK

    def _track_lyrics(self, track_id: str, parsed) -> int:
        if len(parsed.query) > MAX_PLAYLIST_RAW_QUERY_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_lyrics",
                "The lyrics request is too large.",
            )
            return HTTPStatus.BAD_REQUEST
        values = parse_qs(parsed.query, keep_blank_values=True)
        title = (values.get("title") or [""])[0].strip()
        artist = (values.get("artist") or [""])[0].strip()
        album = (values.get("album") or [""])[0].strip()
        raw_duration = (values.get("durationMs") or ["0"])[0]
        if not title or not artist:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_lyrics",
                "Track title and artist are required for lyrics.",
            )
            return HTTPStatus.BAD_REQUEST
        if len(title) > MAX_QUERY_CHARS or len(artist) > MAX_QUERY_CHARS or len(album) > MAX_QUERY_CHARS:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_lyrics",
                "Track metadata is too long for a lyrics lookup.",
            )
            return HTTPStatus.BAD_REQUEST
        try:
            duration_ms = int(raw_duration)
        except (TypeError, ValueError):
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_duration",
                "The track duration must be a whole number.",
            )
            return HTTPStatus.BAD_REQUEST
        if duration_ms < 0:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_duration",
                "The track duration cannot be negative.",
            )
            return HTTPStatus.BAD_REQUEST

        if not self._begin_job():
            return HTTPStatus.SERVICE_UNAVAILABLE
        try:
            result = self.server.lyrics.lookup(track_id, title, artist, album, duration_ms)
        except ValueError:
            self._send_error(
                HTTPStatus.BAD_REQUEST,
                "invalid_lyrics",
                "Invalid lyrics request.",
            )
            return HTTPStatus.BAD_REQUEST
        except LyricsError as error:
            self._send_error(HTTPStatus.BAD_GATEWAY, "lyrics_error", str(error))
            return HTTPStatus.BAD_GATEWAY
        except Exception as error:
            logger.error("Lyrics lookup failed (%s)", type(error).__name__)
            self._send_error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "internal_error",
                "The server could not load lyrics.",
            )
            return HTTPStatus.INTERNAL_SERVER_ERROR
        finally:
            self.server.jobs.release()

        self._send_json(HTTPStatus.OK, result)
        return HTTPStatus.OK

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_error(self, status: int, code: str, message: str, headers=None) -> None:
        body = json.dumps(
            {"error": {"code": code, "message": message}},
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if self.close_connection:
            self.send_header("Connection", "close")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def create_server(
    settings: Settings,
    service=None,
    address=None,
    limiter=None,
    playlists=None,
    lyrics=None,
    library=None,
) -> EmberServer:
    if service is None:
        cache = AudioCache(
            settings.cache_dir,
            settings.cache_max_bytes,
            max_entry_bytes=settings.max_audio_bytes,
        )
        service = MusicService(cache, settings.max_audio_bytes)
    return EmberServer(
        address or (settings.host, settings.port),
        settings,
        service,
        limiter=limiter,
        playlists=playlists,
        lyrics=lyrics,
        library=library,
    )
