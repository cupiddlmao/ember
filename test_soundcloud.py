import unittest
from unittest.mock import patch

import soundcloud


class SoundCloudTests(unittest.TestCase):
    def test_links_are_soundcloud_only(self):
        self.assertEqual(soundcloud.soundcloud_url("https://soundcloud.com/artist/track?si=tracking"), "https://soundcloud.com/artist/track")
        for url in ("http://soundcloud.com/a/b", "https://soundcloud.com.evil.test/a/b", "file:///etc/passwd", "https://user:password@soundcloud.com/a/b"):
            with self.assertRaises(ValueError):
                soundcloud.soundcloud_url(url)

    def info(self, **extra):
        return {"id": "123", "title": "Original", "artist": "Artist", "duration": 60, "webpage_url": "https://soundcloud.com/artist/original", **extra}

    def test_normalize_keeps_source_and_metadata(self):
        track = soundcloud.normalize(self.info())
        self.assertEqual((track["source"], track["duration_ms"], track["artists"][0]["name"]), ("soundcloud", 60000, "Artist"))

    def test_playlist_load_requests_metadata_and_skips_unavailable_entries(self):
        with patch("soundcloud.extract", return_value={"entries": [self.info(), None]}) as extract:
            tracks = soundcloud.search("https://soundcloud.com/artist/sets/list")
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["name"], "Original")
            extract.assert_called_once_with("https://soundcloud.com/artist/sets/list", flat=True, metadata=True)

    def test_preview_is_not_substituted_for_full_track(self):
        with patch("soundcloud.extract", return_value=self.info(formats=[{"format_id": "hls_preview", "url": "https://media.sndcdn.com/preview", "protocol": "m3u8_native"}])):
            with self.assertRaisesRegex(RuntimeError, "No full"):
                soundcloud.resolve({"uri": "https://soundcloud.com/artist/original"})

    def test_unexpected_stream_host_is_blocked(self):
        with patch("soundcloud.extract", return_value=self.info(formats=[{"format_id": "http", "url": "https://ads.example.test/audio", "protocol": "https"}])):
            with self.assertRaisesRegex(RuntimeError, "blocked"):
                soundcloud.resolve({"uri": "https://soundcloud.com/artist/original"})

    def test_stream_resolves_without_downloading_audio(self):
        with patch("soundcloud.extract", return_value=self.info(formats=[{"format_id": "hls_aac", "url": "https://media.sndcdn.com/song.m3u8", "protocol": "m3u8_native"}])):
            track, url = soundcloud.resolve({"uri": "https://soundcloud.com/artist/original"})
            self.assertEqual(track["name"], "Original")
            self.assertTrue(url.endswith("song.m3u8"))


if __name__ == "__main__":
    unittest.main()
