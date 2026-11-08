"""Focused tests for the Ember mobile HTTP API (no network access)."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
import urllib.parse
import uuid
from pathlib import Path

from mobile.backend.config import Settings
from mobile.backend.http_api import RateLimiter, create_server
from mobile.backend.music_service import UpstreamError
from mobile.backend.playlist_service import PlaylistInputError

TOKEN = "test-token-that-is-long-enough-1234"
TRACK = {
    "id": "dQw4w9WgXcQ",
    "title": "Test Song",
    "artist": "Test Artist",
    "album": "Test Album",
    "durationMs": 213000,
    "artworkUrl": "https://example.com/art.jpg",
}


class FakeService:
    def __init__(self, directory: Path):
        self.directory = directory
        self.search_calls = []
        self.audio_calls = []
        self.error = None

    def search(self, query, limit):
        self.search_calls.append((query, limit))
        if self.error:
            raise self.error
        return [dict(TRACK)][:limit]

    def audio_file(self, video_id):
        self.audio_calls.append(video_id)
        if self.error:
            raise self.error
        path = self.directory / f"{video_id}.m4a"
        path.write_bytes(b"fake-m4a-audio")
        return path


class FakePlaylists:
    def __init__(self):
        self.preview_calls = []
        self.page_calls = []
        self.error = None

    def preview(self, url, limit):
        self.preview_calls.append((url, limit))
        if self.error:
            raise self.error
        return self.page("PL1234567890", 0, limit)

    def page(self, playlist_id, offset, limit, query=""):
        self.page_calls.append((playlist_id, offset, limit, query))
        if self.error:
            raise self.error
        return {
            "playlist": {
                "id": playlist_id,
                "title": "Test Playlist",
                "owner": "Tester",
                "artworkUrl": "https://example.com/playlist.jpg",
                "trackCount": 1,
                "unavailableCount": 0,
            },
            "tracks": [dict(TRACK)],
            "offset": offset,
            "limit": limit,
            "total": 1,
            "hasMore": False,
        }


class FakeLyrics:
    def __init__(self):
        self.calls = []
        self.error = None

    def lookup(self, track_id, title, artist, album, duration_ms):
        self.calls.append((track_id, title, artist, album, duration_ms))
        if self.error:
            raise self.error
        return {
            "trackId": track_id,
            "source": "lrclib",
            "kind": "plain",
            "lines": [{"text": "Hello", "startMs": None, "endMs": None, "words": []}],
        }


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.service = FakeService(root)
        self.playlists = FakePlaylists()
        self.lyrics = FakeLyrics()
        settings = Settings(
            token=TOKEN,
            cache_dir=root / "cache",
            library_dir=root / "library",
            cache_max_bytes=8 * 1024 * 1024,
            max_audio_bytes=4 * 1024 * 1024,
            rate_limit_per_minute=1000,
        )
        self.server = create_server(
            settings,
            service=self.service,
            address=("127.0.0.1", 0),
            playlists=self.playlists,
            lyrics=self.lyrics,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self._stop)
        self.port = self.server.server_address[1]

    def _stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, token=TOKEN, method="GET", body=None, extra_headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        if body is not None:
            body = json.dumps(body)
            headers["Content-Type"] = "application/json"
        headers.update(extra_headers or {})
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return response.status, response.getheader("Content-Type"), response.read()
        finally:
            connection.close()

    def error_body(self, body):
        payload = json.loads(body)
        return payload["error"]["code"], payload["error"]["message"]

    def test_health_requires_a_valid_key(self):
        status, content_type, body = self.request("/v1/health")
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        self.assertEqual(json.loads(body)["status"], "ok")

        for token in (None, "wrong-key", TOKEN + "x"):
            status, content_type, body = self.request("/v1/health", token=token)
            self.assertEqual(status, 401)
            self.assertEqual(self.error_body(body)[0], "unauthorized")

    def test_malformed_authorization_is_rejected(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/v1/health", headers={"Authorization": TOKEN})
            response = connection.getresponse()
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()

    def test_search_returns_bounded_tracks(self):
        status, _, body = self.request("/v1/search?q=tycho+awake")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["tracks"], [TRACK])
        self.assertEqual(self.service.search_calls, [("tycho awake", 20)])

    def test_search_limit_is_clamped(self):
        status, _, _ = self.request("/v1/search?q=music&limit=999")
        self.assertEqual(status, 200)
        status, _, _ = self.request("/v1/search?q=music&limit=-4")
        self.assertEqual(status, 200)
        self.assertEqual(self.service.search_calls, [("music", 25), ("music", 1)])

    def test_search_rejects_bad_parameters_without_calling_upstream(self):
        for path, code in (
            ("/v1/search", "invalid_query"),
            ("/v1/search?q=", "invalid_query"),
            ("/v1/search?q=" + "a" * 201, "invalid_query"),
            ("/v1/search?q=music&limit=abc", "invalid_limit"),
        ):
            status, _, body = self.request(path)
            self.assertEqual(status, 400, path)
            self.assertEqual(self.error_body(body)[0], code)
        self.assertEqual(self.service.search_calls, [])

    def test_oversized_raw_query_is_rejected(self):
        status, _, body = self.request("/v1/search?q=" + "a" * 600)
        self.assertEqual(status, 400)
        self.assertEqual(self.error_body(body)[0], "invalid_query")

    def test_audio_response_is_authenticated_audio_mp4(self):
        status, content_type, body = self.request("/v1/tracks/dQw4w9WgXcQ/audio")
        self.assertEqual(status, 200)
        self.assertEqual(content_type, "audio/mp4")
        self.assertEqual(body, b"fake-m4a-audio")
        self.assertEqual(self.service.audio_calls, ["dQw4w9WgXcQ"])

        status, _, body = self.request("/v1/tracks/dQw4w9WgXcQ/audio", token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.service.audio_calls, ["dQw4w9WgXcQ"])

    def test_invalid_track_id_is_not_found(self):
        status, _, body = self.request("/v1/tracks/too-short/audio")
        self.assertEqual(status, 404)
        self.assertEqual(self.error_body(body)[0], "not_found")
        self.assertEqual(self.service.audio_calls, [])

    def test_upstream_errors_are_safe_and_specific(self):
        self.service.error = UpstreamError("YouTube could not provide this audio.")
        status, _, body = self.request("/v1/tracks/dQw4w9WgXcQ/audio")
        self.assertEqual(status, 502)
        code, message = self.error_body(body)
        self.assertEqual(code, "upstream_error")
        self.assertEqual(message, "YouTube could not provide this audio.")
        self.assertNotIn(TOKEN, body.decode())

    def test_internal_errors_do_not_leak_details(self):
        self.service.error = RuntimeError(f"secret {TOKEN} at /private/server/path")
        status, _, body = self.request("/v1/search?q=music")
        self.assertEqual(status, 500)
        code, message = self.error_body(body)
        self.assertEqual(code, "internal_error")
        self.assertNotIn(TOKEN, message)
        self.assertNotIn("private", message)
        self.assertNotIn("RuntimeError", message)

    def test_public_ember_prefix_is_accepted(self):
        status, _, body = self.request("/ember/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["version"], "0.3.0")

    def test_playlist_preview_and_pagination_are_authenticated(self):
        url = "https://music.youtube.com/playlist?list=PL1234567890"
        path = "/v1/playlists/preview?url=" + urllib.parse.quote(url, safe="")
        status, _, body = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["playlist"]["title"], "Test Playlist")
        self.assertEqual(self.playlists.preview_calls, [(url, 50)])

        status, _, _ = self.request("/v1/playlists/PL1234567890?offset=1&limit=2&q=test")
        self.assertEqual(status, 200)
        self.assertEqual(self.playlists.page_calls[-1], ("PL1234567890", 1, 2, "test"))

        status, _, body = self.request(path, token=None)
        self.assertEqual(status, 401)
        self.assertEqual(self.playlists.preview_calls, [(url, 50)])

    def test_playlist_validation_and_safe_upstream_errors(self):
        self.playlists.error = PlaylistInputError("Paste a playlist link.")
        status, _, body = self.request("/v1/playlists/preview?url=not-a-playlist")
        self.assertEqual(status, 400)
        self.assertEqual(self.error_body(body)[0], "invalid_playlist")

        status, _, body = self.request("/v1/playlists/PL1234567890")
        self.assertEqual(status, 400)
        self.assertEqual(self.error_body(body)[0], "invalid_playlist")

        self.playlists.error = UpstreamError("This playlist is unavailable.")
        status, _, body = self.request("/v1/playlists/PL1234567890")
        self.assertEqual(status, 502)
        self.assertEqual(self.error_body(body)[0], "upstream_error")

    def test_lyrics_route_is_authenticated_and_bounded(self):
        path = "/v1/tracks/dQw4w9WgXcQ/lyrics?title=Song&artist=Artist&album=Album&durationMs=213000"
        status, _, body = self.request(path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["kind"], "plain")
        self.assertEqual(self.lyrics.calls[-1], ("dQw4w9WgXcQ", "Song", "Artist", "Album", 213000))

        status, _, _ = self.request(path, token=None)
        self.assertEqual(status, 401)
        status, _, body = self.request("/v1/tracks/dQw4w9WgXcQ/lyrics?title=Song")
        self.assertEqual(status, 400)
        self.assertEqual(self.error_body(body)[0], "invalid_lyrics")

    def test_unknown_routes_and_methods(self):
        status, _, body = self.request("/v1/nope")
        self.assertEqual(status, 404)
        self.assertEqual(self.error_body(body)[0], "not_found")

        status, _, body = self.request("/v1/search?q=music", method="POST")
        self.assertEqual(status, 405)
        self.assertEqual(self.error_body(body)[0], "method_not_allowed")

    def library_create_body(self):
        return {"id": str(uuid.uuid4()), "requestId": str(uuid.uuid4()),
                "name": "GG", "entries": []}

    def test_library_lifecycle_through_prefixed_and_direct_routes(self):
        body = self.library_create_body()
        base = "/v1/library/playlists"
        path = base + "/" + body["id"]
        status, _, raw = self.request("/ember" + base, method="POST", body=body)
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(raw)["revision"], 1)
        self.assertEqual(self.request(base, method="POST", body=body)[0], 201)
        track = {**TRACK, "artworkUrl": "https://i.ytimg.com/a.jpg"}
        entries = [{"id": str(uuid.uuid4()), "track": track} for _ in range(2)]
        edit = {"requestId": str(uuid.uuid4()), "expectedRevision": 1,
                "name": "GG renamed", "entries": entries}
        self.assertEqual(self.request(path, method="PUT", body=edit)[0], 200)
        self.assertEqual(self.request(path, method="PUT", body=edit)[0], 200)
        stale = {**edit, "requestId": str(uuid.uuid4()), "entries": []}
        self.assertEqual(self.request(path, method="PUT", body=stale)[0], 409)
        saved = json.loads(self.request("/ember" + path)[2])
        self.assertEqual(saved["revision"], 2)
        self.assertEqual([item["id"] for item in saved["entries"]], [item["id"] for item in entries])
        listing = json.loads(self.request(base)[2])
        self.assertEqual(listing["playlists"][0]["trackCount"], 2)
        delete = {"requestId": str(uuid.uuid4()), "expectedRevision": 2}
        self.assertEqual(self.request(path, method="DELETE", body=delete)[0], 200)
        self.assertEqual(self.request(path, method="DELETE", body=delete)[0], 200)
        self.assertEqual(self.request(path)[0], 404)
        self.assertEqual(json.loads(self.request(base)[2]), {"playlists": []})
        self.assertEqual(self.service.search_calls, [])
        self.assertEqual(self.playlists.preview_calls, [])

    def test_all_library_routes_require_auth_before_reading_body(self):
        body = self.library_create_body()
        base = "/v1/library/playlists"
        path = base + "/" + body["id"]
        for method, route in [("GET", base), ("GET", path), ("POST", base), ("PUT", path), ("DELETE", path)]:
            self.assertEqual(self.request(route, token=None, method=method)[0], 401)
        self.assertEqual(self.server.library.list(), {"playlists": []})

    def test_library_rejects_invalid_bodies_and_methods(self):
        base = "/v1/library/playlists"
        self.assertEqual(self.request(base, method="PATCH")[0], 405)
        self.assertEqual(self.request(base, method="POST", body={})[0], 400)
        self.assertEqual(self.request(base, method="POST", body=self.library_create_body(),
                                      extra_headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request(base, method="POST", extra_headers={
            "Content-Length": str(8 * 1024 * 1024 + 1), "Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.request(base, method="POST", extra_headers={"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.request(base, method="POST", body=[])[0], 400)
        self.assertEqual(self.request(base, method="POST", body=float("nan"))[0], 400)

    def test_library_responses_are_not_cacheable(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request("GET", "/v1/library/playlists", headers={"Authorization": "Bearer " + TOKEN})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Cache-Control"), "no-store")
            response.read()
        finally:
            connection.close()


class RateLimiterTests(unittest.TestCase):
    def test_limits_each_client_independently(self):
        limiter = RateLimiter(2, window=60)
        self.assertTrue(limiter.allow("one"))
        self.assertTrue(limiter.allow("one"))
        self.assertFalse(limiter.allow("one"))
        self.assertTrue(limiter.allow("two"))


if __name__ == "__main__":
    unittest.main()
