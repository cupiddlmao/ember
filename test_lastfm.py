import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import lastfm


class LastfmTests(unittest.TestCase):
    def test_signature_sorts_and_excludes_response_parameters(self):
        params = {
            "token": "token",
            "method": "auth.getSession",
            "api_key": "key",
            "format": "json",
            "callback": "ignored",
        }
        expected = hashlib.md5(b"api_keykeymethodauth.getSessiontokentokensecret").hexdigest()
        self.assertEqual(lastfm.signature(params, "secret"), expected)

    def test_authorize_url_encodes_key_and_token(self):
        self.assertEqual(
            lastfm.authorize_url("api key", "tok/en"),
            "https://www.last.fm/api/auth/?api_key=api+key&token=tok%2Fen",
        )

    def test_token_session_and_track_requests(self):
        with patch("lastfm._request", return_value={"token": "new-token"}) as request:
            self.assertEqual(lastfm.request_token("key", "secret"), "new-token")
        request.assert_called_once_with({"api_key": "key", "method": "auth.getToken"}, "secret")

        with patch("lastfm._request", return_value={"session": {"name": "listener", "key": "session"}}) as request:
            self.assertEqual(lastfm.request_session("key", "secret", "new-token"), {"name": "listener", "key": "session"})
        request.assert_called_once_with(
            {"api_key": "key", "method": "auth.getSession", "token": "new-token"}, "secret"
        )

        config = {"api_key": "key", "api_secret": "secret", "session_key": "session"}
        with patch("lastfm._request", return_value={}) as request:
            lastfm.now_playing(config, artist="Artist", track="Track", album="Album", duration=123)
        self.assertEqual(request.call_args.args[0], {
            "api_key": "key",
            "method": "track.updateNowPlaying",
            "sk": "session",
            "artist": "Artist",
            "track": "Track",
            "album": "Album",
            "duration": 123,
        })
        self.assertEqual(request.call_args.args[1], "secret")

        with patch("lastfm._request", return_value={}) as request:
            lastfm.scrobble(config, artist="Artist", track="Track", timestamp=123456, duration=123)
        self.assertEqual(request.call_args.args[0]["timestamp"], 123456)

    def test_signed_post_body(self):
        import io
        import urllib.parse
        with patch("lastfm.urllib.request.urlopen", return_value=io.BytesIO(b'{"token":"new-token"}')) as fetch:
            self.assertEqual(lastfm.request_token("key", "secret"), "new-token")
        request = fetch.call_args.args[0]
        body = urllib.parse.parse_qs(request.data.decode("utf-8"))
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(body["method"], ["auth.getToken"])
        self.assertEqual(body["format"], ["json"])
        self.assertEqual(body["api_sig"], [lastfm.signature({"api_key": "key", "method": "auth.getToken"}, "secret")])

    def test_scrobble_threshold(self):
        self.assertIsNone(lastfm.scrobble_threshold(30))
        self.assertEqual(lastfm.scrobble_threshold(40), 20)
        self.assertEqual(lastfm.scrobble_threshold(600), 240)
        self.assertTrue(lastfm.should_scrobble(40, 20))
        self.assertFalse(lastfm.should_scrobble(40, 19.9))
        self.assertFalse(lastfm.should_scrobble(0, 1000))
        self.assertFalse(lastfm.should_scrobble(40, "not a position"))

    def test_private_storage_and_connection_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lastfm.json"
            saved = lastfm.save({
                "api_key": "key",
                "api_secret": "secret",
                "session_key": "session",
                "username": "listener",
                "ignored": "not persisted",
            }, path=path)
            self.assertEqual(lastfm.load(path), saved)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)
            self.assertTrue(lastfm.connected(saved))
            self.assertFalse(lastfm.connected({"api_key": "key", "api_secret": "secret"}))


if __name__ == "__main__":
    unittest.main()
