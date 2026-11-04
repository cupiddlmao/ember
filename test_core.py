import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from lyrics import import_lyrics, interval, parse_lrc, parse_ttml, seconds, spicy_lines
from services import SoundCloudAccount, Spotify, callback_code, load_private, save_private


class LyricsTests(unittest.TestCase):
    def test_timestamp_formats_and_rejection(self):
        self.assertEqual(seconds("01:02.5"), 62.5)
        self.assertEqual(seconds("250ms"), .25)
        self.assertEqual(seconds("1:02:03"), 3723)
        for value in ("nan", "inf", "-1", "garbage"):
            with self.assertRaises(ValueError):
                seconds(value)
        with self.assertRaises(ValueError):
            interval(5, 2)

    def test_lrc_repeated_stamps_and_offset(self):
        lines = parse_lrc("[offset:-500]\n[00:01.00][00:03.00]Hello\n[00:05.00]Goodbye")
        self.assertEqual([(line.text, line.start) for line in lines], [("Hello", .5), ("Hello", 2.5), ("Goodbye", 4.5)])
        self.assertEqual(lines[0].end, 2.5)
        self.assertEqual(lines[0].words, [])

    def test_ttml_word_timing_and_whitespace(self):
        lines = parse_ttml('<tt xmlns="http://www.w3.org/ns/ttml"><body><div><p begin="1s" end="4s"><span begin="1s" end="2s">Hello</span> <span begin="2s" end="4s">world</span></p></div></body></tt>')
        self.assertEqual(lines[0].text, "Hello world")
        self.assertEqual(lines[0].words[0].text, "Hello ")
        self.assertEqual(lines[0].words[1].start, 2)
        with self.assertRaises(ValueError):
            parse_ttml('<!DOCTYPE tt [<!ENTITY x "secret">]><tt/>')

    def test_spicy_syllables_duet_and_background(self):
        group = {"StartTime": 1, "EndTime": 3, "Syllables": [{"Text": "Hel", "StartTime": 1, "EndTime": 1.5, "IsPartOfWord": True}, {"Text": "lo", "StartTime": 1.5, "EndTime": 2}, {"Text": "world", "StartTime": 2, "EndTime": 3}]}
        lines = spicy_lines({"Type": "Syllable", "Content": [{"Type": "Vocal", "Lead": group, "Background": [group], "OppositeAligned": True}]})
        self.assertEqual(lines[0].text, "Hello world")
        self.assertTrue(lines[0].opposite)
        self.assertTrue(lines[1].background)
        self.assertEqual(lines[0].words[0].text, "Hel")

    def test_line_and_static_formats(self):
        self.assertEqual(spicy_lines({"Type": "Static", "Lines": [{"Text": "Hello"}]})[0].start, -1)
        lines = spicy_lines({"Type": "Line", "Content": [{"Type": "Vocal", "StartTime": 2, "EndTime": 4, "Text": "Hello"}]})
        self.assertEqual(lines[0].words, [])
        with self.assertRaises(ValueError):
            spicy_lines({"Type": "unsupported"})

    def test_import_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.lrc"
            path.write_text("\ufeff[00:01]Hello")
            self.assertEqual(import_lyrics(path)[0].text, "Hello")
            path.write_bytes(b"x" * 2_000_001)
            with self.assertRaises(ValueError):
                import_lyrics(path)


class AuthTests(unittest.TestCase):
    def test_callback_state_and_path(self):
        self.assertEqual(callback_code("/callback?state=abc&code=good", "abc"), "good")
        for url in ("/callback?state=wrong&code=good", "/other?state=abc&code=good", "/callback?state=abc"):
            with self.assertRaises(ValueError):
                callback_code(url, "abc")
        with self.assertRaises(RuntimeError):
            callback_code("/callback?state=abc&error=access_denied", "abc")

    def test_private_storage_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private" / "session.json"
            save_private(path, {"example": "value"})
            self.assertEqual(load_private(path), {"example": "value"})
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(path.parent).st_mode & 0o777, 0o700)

    def test_refresh_preserves_refresh_token_and_logout(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.DATA", Path(directory)):
            client = Spotify()
            client.configure("a" * 32, "")
            client.tokens = {"refresh_token": "test-refresh", "expires_at": 0}
            with patch("services.request_json", return_value={"access_token": "test-access", "expires_in": 3600}) as request:
                self.assertEqual(client.access_token(), "test-access")
                self.assertEqual(client.access_token(), "test-access")
                self.assertEqual(request.call_count, 1)
                self.assertEqual(client.tokens["refresh_token"], "test-refresh")
            client.logout()
            self.assertFalse((Path(directory) / "session.json").exists())
            with self.assertRaises(RuntimeError):
                client.access_token()


class SoundCloudAuthTests(unittest.TestCase):
    def test_refresh_rotates_and_uses_separate_private_session(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.DATA", Path(directory)):
            client = SoundCloudAccount()
            client.configure("example-client", "example-secret")
            client.tokens = {"refresh_token": "old", "expires_at": 0}
            with patch("services.request_json", return_value={"access_token": "access", "refresh_token": "new", "expires_in": 3600}) as request:
                self.assertEqual(client.access_token(), "access")
                self.assertEqual(client.tokens["refresh_token"], "new")
                self.assertEqual(request.call_args.args[0], "https://secure.soundcloud.com/oauth/token")
                self.assertEqual(request.call_args.kwargs["form"]["client_secret"], "example-secret")
            self.assertTrue((Path(directory) / "soundcloud-session.json").exists())
            self.assertFalse((Path(directory) / "session.json").exists())
            client.configure("different-client", "example-secret")
            self.assertFalse(client.tokens)

    def test_pagination_and_token_host_boundary(self):
        with tempfile.TemporaryDirectory() as directory, patch("services.DATA", Path(directory)):
            client = SoundCloudAccount()
            with patch.object(client, "access_token", return_value="test-token"), patch("services.request_json", side_effect=[{"collection": [{"id": 1}], "next_href": "https://api.soundcloud.com/me/playlists?offset=1"}, {"collection": [{"id": 2}]}]) as request:
                self.assertEqual([p["id"] for p in client.playlists()], [1, 2])
                self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "OAuth test-token")
                for path in ("https://evil.example/", "//evil.example/", "http://api.soundcloud.com/me"):
                    with self.assertRaises(ValueError):
                        client.api(path)
                self.assertEqual(request.call_count, 2)


if __name__ == "__main__":
    unittest.main()
