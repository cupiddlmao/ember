import http.client
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch, MagicMock

from mobile.backend.config import Settings
from mobile.backend.http_api import create_server
from mobile.backend.lastfm_service import LastFM
from mobile.backend.library_store import LibraryError
from mobile.backend.music_service import MusicService


class LastFMTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(token="test-only-token-long-enough", library_dir=Path(self.temp.name) / "library",
                                 cache_dir=Path(self.temp.name) / "cache", lastfm_api_key="test-key", lastfm_api_secret="test-secret")
        self.calls = []
        def call(method, **params):
            self.calls.append((method, params))
            if method == "auth.getToken": return {"token": "test-token"}
            if method == "auth.getSession": return {"session": {"key": "test-session", "name": "test-user"}}
            if method == "track.scrobble": return {"scrobbles": {"@attr": {"accepted": "1"}}}
            return {}
        self.call = call
        # No racing background delivery; exercise flush deterministically.
        with patch.object(threading.Thread, "start"):
            self.service = LastFM(self.settings, call)

    def tearDown(self):
        self.temp.cleanup()

    def connect(self):
        url = self.service.handle({"action": "connect"})["url"]
        self.assertTrue(url.startswith("https://www.last.fm/api/auth/?"))
        self.service.handle({"action": "finish"})

    def event(self):
        return dict(action="scrobble", id=str(uuid.uuid4()), title="Song", artist="Artist", duration=180,
                    listened=90, started=int(time.time()))

    def test_auth_status_disconnect_keeps_session_private(self):
        self.connect()
        status = self.service.handle({"action": "status"})
        self.assertTrue(status["connected"])
        self.assertNotIn("test-session", json.dumps(status))
        self.service.handle({"action": "disconnect"})
        self.assertFalse(self.service.handle({"action": "status"})["connected"])

    def test_threshold_short_tracks_and_bad_metadata(self):
        self.connect()
        for changes in ({"listened":89}, {"duration":30}, {"listened":float("nan")}, {"started":0}, {"artist":""}, {"id":"invalid"}):
            with self.subTest(changes=changes), self.assertRaises(LibraryError):
                self.service.handle(self.event() | changes)
        self.assertEqual(self.service.handle({"action":"status"})["pending"], 0)
        self.service.handle(self.event() | {"duration":900, "listened":240})

    def test_retries_are_durable_and_event_ids_are_idempotent(self):
        self.connect()
        event = self.event()
        self.service.handle(event)
        self.service.handle(event)
        with self.assertRaises(LibraryError):
            self.service.handle(event | {"title":"Different"})
        self.service.call = lambda *a, **kw: (_ for _ in ()).throw(OSError("offline"))
        with self.assertRaises(OSError): self.service.flush()
        self.assertEqual(self.service.handle({"action":"status"})["pending"], 1)
        with patch.object(threading.Thread, "start"):
            restored = LastFM(self.settings, self.call)
        restored.flush()
        restored.handle(event)
        restored.flush()
        self.assertEqual(restored.handle({"action":"status"})["pending"], 0)
        self.assertEqual(sum(m == "track.scrobble" for m, _ in self.calls), 1)
        self.assertEqual(self.calls[-1][1]["timestamp"], event["started"])

    def test_disconnected_rejects_listens_and_clears_queue(self):
        with self.assertRaises(LibraryError): self.service.handle(self.event())
        self.connect()
        self.service.handle(self.event())
        self.service.handle({"action":"disconnect"})
        self.assertEqual(self.service.handle({"action":"status"})["pending"], 0)

    def test_signed_requests_and_sanitized_errors(self):
        from io import BytesIO
        from urllib.parse import parse_qs
        import hashlib
        def upstream(request, timeout):
            params = {k: v[0] for k, v in parse_qs(request.data.decode()).items()}
            signature = params.pop("api_sig")
            self.assertEqual(params.pop("format"), "json")
            raw = "".join(k + params[k] for k in sorted(params)) + "test-secret"
            self.assertEqual(signature, hashlib.md5(raw.encode()).hexdigest())
            self.assertEqual(timeout, 10)
            return BytesIO(b'{"token":"safe-test-token"}')
        with patch("mobile.backend.lastfm_service.urlopen", upstream):
            self.assertEqual(self.service._call("auth.getToken"), {"token":"safe-test-token"})
        with patch("mobile.backend.lastfm_service.urlopen", side_effect=OSError("test-secret")):
            with self.assertRaises(LibraryError) as error:
                self.service._call("auth.getToken")
            self.assertNotIn("test-secret", str(error.exception))

    def test_permanent_upstream_rejection_does_not_block_queue(self):
        self.connect()
        self.service.handle(self.event())
        self.service.call = lambda *a, **kw: {"scrobbles": {"@attr":{"accepted":"0"}, "scrobble":{"ignoredMessage":{"code":"2"}}}}
        self.service.flush()
        self.assertEqual(self.service.handle({"action":"status"})["pending"], 0)

    def test_album_forwarding_survives_restart_and_old_events_still_work(self):
        self.connect()
        self.service.handle(dict(action="now", title="Song", artist="Artist", album=" Album "))
        self.assertEqual(self.calls[-1][1]["album"], "Album")
        event = self.event() | {"album":"Album"}
        self.service.handle(event)
        self.service.handle(event)
        with self.assertRaises(LibraryError):
            self.service.handle(event | {"album":"Different"})
        with patch.object(threading.Thread, "start"):
            restored = LastFM(self.settings, self.call)
        restored.flush()
        self.assertEqual(self.calls[-1][1]["album"], "Album")
        old = self.event()
        restored.handle(old)
        restored.flush()
        self.assertNotIn("album", self.calls[-1][1])
        restored.handle(old)
        self.assertEqual(restored.handle({"action":"status"})["pending"], 0)

    def test_invalid_album_is_rejected_and_blank_album_is_omitted(self):
        self.connect()
        for album in (None, 123, "x" * 501, "bad\nname"):
            with self.subTest(album=album), self.assertRaises(LibraryError):
                self.service.handle(self.event() | {"album":album})
        self.service.handle(dict(action="now", title="Song", artist="Artist", album=" "))
        self.assertNotIn("album", self.calls[-1][1])

    def test_radio_recommendations_need_no_connected_account(self):
        self.service.call = lambda method, **params: {"similartracks": {"track": [
            {"name": "Song", "artist": {"name": "Artist"}},
            {"name": "Song", "artist": {"name": "Artist"}},
            {"name": "Bad", "artist": "invalid"},
        ]}}
        self.assertEqual(self.service.recommendations({"title": "Seed", "artist": "Artist"}), [{"title": "Song", "artist": "Artist"}])
        self.assertFalse(self.service.handle({"action": "status"})["connected"])
        with self.assertRaises(LibraryError):
            self.service.recommendations({"title": "", "artist": "Artist"})
        self.service.key = ""
        with self.assertRaises(LibraryError) as error:
            self.service.recommendations({"title": "Seed", "artist": "Artist"})
        self.assertEqual(error.exception.status, 503)

    def test_radio_matches_identity_skips_wrong_versions_and_deduplicates(self):
        catalog = MagicMock()
        catalog.search.return_value = [
            None,
            {"videoId": "invalid", "title": "Song"},
            {"videoId": "aaaaaaaaaaa", "title": "Song (Live)", "artists": [{"name": "Artist"}]},
            {"videoId": "bbbbbbbbbbb", "title": "Song", "artists": [{"name": "Wrong Artist"}]},
            {"videoId": "ccccccccccc", "title": "Song", "artists": [{"name": "Artist"}], "duration": "3:00"},
        ]
        service = MusicService(None, 1000)
        with patch("music.YTMusic", return_value=catalog):
            tracks = service.radio([{"title": "Song", "artist": "Artist"}] * 2)
            self.assertEqual([t["id"] for t in tracks], ["ccccccccccc"])
            catalog.search.side_effect = TimeoutError()
            self.assertEqual(service.radio([{"title": "Song", "artist": "Artist"}]), [])

    def test_radio_http_uses_lastfm_and_keeps_credentials_private(self):
        music_service = MagicMock()
        music_service.radio.return_value = []
        server = create_server(self.settings, service=music_service, address=("127.0.0.1", 0))
        server.lastfm.call = lambda method, **params: {"similartracks": {"track": [{"name": "Song", "artist": {"name": "Artist"}}]}}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for token, body, expected in [("", {"action":"radio"}, 401),
                    (self.settings.token, {"action":"radio", "title":"Seed", "artist":"Artist"}, 200),
                    (self.settings.token, [], 400)]:
                conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                conn.request("POST", "/ember/v1/lastfm", json.dumps(body), {"Authorization":"Bearer "+token, "Content-Type":"application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                raw = response.read().decode()
                self.assertNotIn("test-secret", raw)
                conn.close()
            music_service.radio.assert_called_once_with([{"title":"Song", "artist":"Artist"}])
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_http_auth_and_bounded_body(self):
        server = create_server(self.settings, service=object(), address=("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for token, data, expected in (("", {"action":"status"},401), (self.settings.token, {"action":"status"},200),
                                          (self.settings.token, {"padding":"x"*5000},413)):
                conn = http.client.HTTPConnection(*server.server_address, timeout=5)
                conn.request("POST", "/ember/v1/lastfm", json.dumps(data), {"Authorization":"Bearer "+token, "Content-Type":"application/json"})
                response = conn.getresponse()
                self.assertEqual(response.status, expected)
                response.read()
                conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__": unittest.main()
