import json
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bridge
import music

TRACK = {"id": "dm4tkSNKfFI", "uri": "https://music.youtube.com/watch?v=dm4tkSNKfFI",
         "name": "Awake", "artists": [{"name": "Tycho"}], "album": {"name": "Awake"}, "duration_ms": 284000}

class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"XDG_CACHE_HOME": self.temp.name})
        self.env.start()
    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()
    def download(self, track, directory):
        path = Path(directory) / "audio.webm"
        path.write_bytes(b"fake audio")
        return track, path
    def test_cold_cache_repeat_hit_disabled_and_clear(self):
        self.assertIsNone(bridge.cached_audio(TRACK))
        with patch.object(music, "download_audio", side_effect=self.download) as download:
            bridge.cache_action("cache", TRACK)
            uri = bridge.cached_audio(TRACK)
            self.assertTrue(uri.startswith("file:///"))
            bridge.cache_action("cache", TRACK)
            self.assertEqual(download.call_count, 1)
            (bridge.cache_root() / "settings.json").write_text(json.dumps({"enabled": False}))
            bridge.cache_action("clear_cache")
            self.assertIsNone(bridge.cached_audio(TRACK))
            bridge.cache_action("cache", TRACK)
            self.assertEqual(download.call_count, 1)
    def test_resolve_uses_cache_before_network_and_falls_back_on_miss(self):
        request = {"action": "resolve", "track": {**TRACK, "artists": ["Tycho"], "album": "Awake"}}
        with patch.object(music, "download_audio", side_effect=self.download):
            bridge.cache_action("cache", TRACK)
        output = io.StringIO()
        with patch.object(bridge, "read_request", return_value=request), patch.object(music, "resolve") as resolve:
            bridge.run(output)
            resolve.assert_not_called()
        payload = json.loads(output.getvalue())["data"]
        self.assertEqual(payload["track"]["artists"], ["Tycho"])
        self.assertTrue(payload["stream_url"].startswith("file:///"))
        bridge.cache_action("clear_cache")
        output = io.StringIO()
        with patch.object(bridge, "read_request", return_value=request), patch.object(music, "resolve", return_value=(TRACK, "https://audio.googlevideo.com/audio")) as resolve:
            bridge.run(output)
            resolve.assert_called_once()
        self.assertEqual(json.loads(output.getvalue())["data"]["stream_url"], "https://audio.googlevideo.com/audio")

    def test_cache_storage_failure_does_not_block_stream_resolution(self):
        with patch.object(bridge, "cache_store", side_effect=OSError("disk unavailable")):
            self.assertIsNone(bridge.cached_audio(TRACK))
    def test_eviction_and_interrupted_download_cleanup(self):
        store = bridge.cache_store()
        store.limit = 10
        second = dict(TRACK, id="abcdefghijk", uri="https://music.youtube.com/watch?v=abcdefghijk")
        with patch.object(music, "download_audio", side_effect=self.download):
            store.download(TRACK)
            store.download(second)
        self.assertIsNone(store.lookup(TRACK))
        self.assertIsNotNone(store.lookup(second))
        orphan = store.cache_root / "ab" / "orphan.webm"
        orphan.parent.mkdir(exist_ok=True)
        orphan.write_bytes(b"orphan")
        abandoned = store.cache_root / "tmp" / "audio-abandoned"
        abandoned.mkdir(parents=True)
        (abandoned / "part").write_bytes(b"partial")
        bridge.cache_action("cache", second)
        self.assertFalse(orphan.exists())
        self.assertFalse(abandoned.exists())
        self.assertIsNotNone(store.lookup(second))
    def test_missing_cached_file_is_a_miss(self):
        with patch.object(music, "download_audio", side_effect=self.download):
            bridge.cache_action("cache", TRACK)
        bridge.cache_store().lookup(TRACK)["path"].unlink()
        self.assertIsNone(bridge.cached_audio(TRACK))

if __name__ == "__main__":
    unittest.main()
