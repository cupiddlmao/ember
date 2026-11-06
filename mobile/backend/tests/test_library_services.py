"""Focused, network-free tests for playlist paging and lyrics timing."""
from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from mobile.backend.lyrics_service import (
    LyricsError,
    LyricsService,
    parse_lrc,
    parse_lyricsfile,
)
from mobile.backend.music_service import UpstreamError
from mobile.backend.playlist_service import PlaylistInputError, PlaylistService

PLAYLIST = {
    "kind": "playlist",
    "playlist": {
        "id": "PL1234567890",
        "title": "Night Drive",
        "owner": "Ember",
        "images": [
            {"url": "https://example.com/small.jpg", "width": 100, "height": 100},
            {"url": "https://example.com/large.jpg", "width": 800, "height": 800},
        ],
    },
    "tracks": [
        {
            "id": "aaaaaaaaaaa",
            "name": "Awake",
            "artists": [{"name": "Tycho"}],
            "album": {"name": "Awake"},
            "duration_ms": 240000,
        },
        {
            "id": "bbbbbbbbbbb",
            "name": "Dive",
            "artists": [{"name": "Tycho"}],
            "album": {"name": "Dive"},
            "duration_ms": 300000,
        },
        {
            "id": "aaaaaaaaaaa",
            "name": "Awake",
            "artists": [{"name": "Tycho"}],
            "album": {"name": "Awake"},
            "duration_ms": 240000,
        },
        {
            "id": "ccccccccccc",
            "name": "Elsewhere",
            "artists": [{"name": "Tycho"}],
            "album": {"name": "Elsewhere"},
            "duration_ms": 180000,
        },
    ],
    "returned": 5,
    "unavailable": 1,
}


class PlaylistServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = PlaylistService(cache_items=2)

    def test_preview_and_pagination_keep_duplicates_and_report_unavailable(self):
        with patch("mobile.backend.playlist_service.music.search", return_value=PLAYLIST) as search:
            page = self.service.preview("https://music.youtube.com/playlist?list=PL1234567890", 2)
            self.assertEqual([track["id"] for track in page["tracks"]], ["aaaaaaaaaaa", "bbbbbbbbbbb"])
            self.assertEqual(page["playlist"]["title"], "Night Drive")
            self.assertEqual(page["playlist"]["artworkUrl"], "https://example.com/large.jpg")
            self.assertEqual(page["playlist"]["trackCount"], 4)
            self.assertEqual(page["playlist"]["unavailableCount"], 1)
            self.assertEqual(page["total"], 4)
            self.assertTrue(page["hasMore"])

            second = self.service.page("PL1234567890", offset=2, limit=2)
            self.assertEqual([track["id"] for track in second["tracks"]], ["aaaaaaaaaaa", "ccccccccccc"])
            self.assertFalse(second["hasMore"])
            search.assert_called_once()

    def test_search_points_at_original_positions(self):
        with patch("mobile.backend.playlist_service.music.search", return_value=PLAYLIST):
            self.service.preview("https://music.youtube.com/playlist?list=PL1234567890")
            page = self.service.page("PL1234567890", query="awake")
            self.assertEqual([track["id"] for track in page["tracks"]], ["aaaaaaaaaaa", "aaaaaaaaaaa"])
            self.assertEqual(page["total"], 2)
            page = self.service.page("PL1234567890", query="tycho dive")
            self.assertEqual([track["id"] for track in page["tracks"]], ["bbbbbbbbbbb"])

    def test_invalid_links_and_provider_failures_are_safe(self):
        with self.assertRaises(PlaylistInputError):
            self.service.preview("https://music.youtube.com/watch?v=dQw4w9WgXcQ")
        with self.assertRaises(PlaylistInputError):
            self.service.preview("not a URL")
        with patch("mobile.backend.playlist_service.music.search", side_effect=RuntimeError("secret")) as search:
            with self.assertRaises(UpstreamError) as raised:
                self.service.preview("https://music.youtube.com/playlist?list=PL1234567890")
            self.assertNotIn("secret", str(raised.exception))
            search.assert_called_once()

    def test_excessive_playlists_fail_instead_of_truncating(self):
        service = PlaylistService(max_tracks=2)
        with patch("mobile.backend.playlist_service.music.search", return_value=PLAYLIST):
            with self.assertRaises(UpstreamError):
                service.preview("https://music.youtube.com/playlist?list=PL1234567890")

    def test_page_values_are_bounded(self):
        with patch("mobile.backend.playlist_service.music.search", return_value=PLAYLIST):
            self.service.preview("https://music.youtube.com/playlist?list=PL1234567890")
            page = self.service.page("PL1234567890", offset=-10, limit=999)
            self.assertEqual(page["offset"], 0)
            self.assertEqual(page["limit"], 100)
            with self.assertRaises(PlaylistInputError):
                self.service.page("PL1234567890", query="x" * 201)


class LyricsParserTests(unittest.TestCase):
    def test_lrc_lines_use_supplied_timestamps_and_ignore_word_tags(self):
        lines = parse_lrc(
            "[offset:+500]\n[00:01.00]Hello <00:01.10>world\n[00:03.50]Again"
        )
        self.assertEqual([line["startMs"] for line in lines], [1500, 4000])
        self.assertEqual(lines[0]["text"], "Hello world")
        self.assertEqual(lines[0]["endMs"], 4000)
        self.assertEqual(lines[0]["words"], [])
        self.assertEqual(lines[1]["endMs"], None)

    def test_lyricsfile_word_timings_are_only_what_the_provider_supplied(self):
        source = """
version: '1.0'
lines:
  - text: 'Hello world'
    start_ms: 1200
    end_ms: 2800
    words:
      - text: 'Hello '
        start_ms: 1200
        end_ms: 1900
      - text: 'world'
        start_ms: 1900
plain: |
  Hello world
"""
        lines = parse_lyricsfile(source)
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["startMs"], 1200)
        self.assertEqual(lines[0]["endMs"], 2800)
        self.assertEqual([word["startMs"] for word in lines[0]["words"]], [1200, 1900])
        self.assertIsNone(lines[0]["words"][1]["endMs"])

    def test_missing_word_end_is_not_invented(self):
        source = """
lines:
  - text: 'One two'
    start_ms: 1000
    words:
      - text: 'One '
        start_ms: 1000
      - text: 'two'
        start_ms: 1500
"""
        words = parse_lyricsfile(source)[0]["words"]
        self.assertEqual([word["endMs"] for word in words], [None, None])

    def test_aliases_and_malformed_timings_are_rejected(self):
        self.assertEqual(parse_lyricsfile("lines: &x\n  - text: Hi\n    start_ms: 1\ncopy: *x\n"), [])
        self.assertEqual(parse_lyricsfile("lines:\n  - text: Bad\n    start_ms: -2\n"), [])
        self.assertEqual(parse_lyricsfile("not: [valid"), [])


class FakeResponse:
    def __init__(self, payload, content_type="application/json"):
        self.buffer = io.BytesIO(json.dumps(payload).encode())
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.buffer.read(size)


class LyricsServiceTests(unittest.TestCase):
    def test_plain_and_instrumental_fallbacks(self):
        service = LyricsService(opener=lambda *_args, **_kwargs: FakeResponse({
            "instrumental": False,
            "syncedLyrics": None,
            "plainLyrics": "One\nTwo",
        }))
        result = service.lookup("dQw4w9WgXcQ", "Song", "Artist", duration_ms=1000)
        self.assertEqual(result["kind"], "plain")
        self.assertIsNone(result["lines"][0]["startMs"])
        self.assertEqual(result["lines"][0]["words"], [])

        service = LyricsService(opener=lambda *_args, **_kwargs: FakeResponse({
            "instrumental": True,
            "plainLyrics": None,
            "syncedLyrics": None,
        }))
        result = service.lookup("dQw4w9WgXcQ", "Song", "Artist")
        self.assertEqual(result["kind"], "instrumental")
        self.assertEqual(result["lines"], [])

    def test_word_sync_uses_lyricsfile(self):
        service = LyricsService(opener=lambda *_args, **_kwargs: FakeResponse({
            "instrumental": False,
            "hasWordSync": True,
            "syncedLyrics": "[00:01.00]Hello world",
            "plainLyrics": None,
            "lyricsfile": "lines:\n  - text: Hello world\n    start_ms: 1000\n    words:\n      - text: Hello \n        start_ms: 1000\n        end_ms: 1500\n      - text: world\n        start_ms: 1500\n",
        }))
        result = service.lookup("dQw4w9WgXcQ", "Song", "Artist")
        self.assertEqual(result["kind"], "synced")
        self.assertEqual(result["lines"][0]["words"][0]["endMs"], 1500)

    def test_404_is_honest_none(self):
        def missing(*_args, **_kwargs):
            raise urllib.error.HTTPError("https://lrclib.net", 404, "not found", {}, None)

        result = LyricsService(opener=missing).lookup("dQw4w9WgXcQ", "Song", "Artist")
        self.assertEqual(result["kind"], "none")
        self.assertEqual(result["lines"], [])

    def test_rate_limit_is_a_safe_error(self):
        def limited(*_args, **_kwargs):
            raise urllib.error.HTTPError("https://lrclib.net", 429, "limited", {"Retry-After": "12"}, None)

        with self.assertRaises(LyricsError) as raised:
            LyricsService(opener=limited).lookup("dQw4w9WgXcQ", "Song", "Artist")
        self.assertIn("rate limited", str(raised.exception))

    def test_invalid_track_id_is_rejected_before_network(self):
        with self.assertRaises(ValueError):
            LyricsService().lookup("bad", "Song", "Artist")


if __name__ == "__main__":
    unittest.main()
