"""Tests for the bounded audio cache and real AAC conversion."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from mobile.backend.audio_cache import AudioCache
from mobile.backend.music_service import MusicService, _convert_to_m4a


class AudioCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "cache"

    def test_entries_are_reused_and_written_privately(self):
        cache = AudioCache(self.root, max_bytes=1024 * 1024)
        calls = []

        def create(work):
            calls.append(1)
            path = work / "audio.m4a"
            path.write_bytes(b"m4a" * 100)
            return path

        first = cache.get_or_create("dQw4w9WgXcQ", create)
        second = cache.get_or_create("dQw4w9WgXcQ", create)
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), b"m4a" * 100)
        self.assertEqual(len(calls), 1)
        self.assertEqual(first.stat().st_mode & 0o777, 0o600)

    def test_concurrent_requests_convert_once(self):
        cache = AudioCache(self.root, max_bytes=1024 * 1024)
        calls = []
        lock = threading.Lock()

        def create(work):
            with lock:
                calls.append(1)
            time.sleep(0.05)
            path = work / "audio.m4a"
            path.write_bytes(b"x" * 256)
            return path

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: cache.get_or_create("abcdefghijk", create), range(6)))
        self.assertTrue(all(result.read_bytes() == b"x" * 256 for result in results))
        self.assertEqual(len(calls), 1)

    def test_eviction_respects_the_byte_budget(self):
        cache = AudioCache(self.root, max_bytes=1000)

        def create(size):
            def builder(work):
                path = work / "audio.m4a"
                path.write_bytes(b"a" * size)
                return path
            return builder

        first = cache.get_or_create("trackone001", create(600))
        self.assertTrue(first.exists())
        time.sleep(0.02)
        cache.get_or_create("tracktwo002", create(600))
        self.assertFalse(first.exists())
        self.assertEqual(sum(path.stat().st_size for path in self.root.glob("*.m4a")), 600)

    def test_oversized_entry_is_rejected(self):
        cache = AudioCache(self.root, max_bytes=1024 * 1024, max_entry_bytes=100)

        def create(work):
            path = work / "audio.m4a"
            path.write_bytes(b"x" * 101)
            return path

        with self.assertRaises(RuntimeError):
            cache.get_or_create("abcdefghijk", create)
        self.assertEqual(list(self.root.glob("*.m4a")), [])

    def test_failed_generation_cleans_its_work_directory(self):
        cache = AudioCache(self.root, max_bytes=1024 * 1024)

        def create(_work):
            raise RuntimeError("conversion failed")

        with self.assertRaises(RuntimeError):
            cache.get_or_create("abcdefghijk", create)
        self.assertEqual(list(self.root.iterdir()), [])


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class ConversionTests(unittest.TestCase):
    def test_m4a_download_has_a_separate_conversion_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "abcdefghijk.m4a"
            subprocess.run(
                [shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                 "-f", "lavfi", "-i", "sine=frequency=440:duration=0.1", str(source)],
                check=True, capture_output=True, timeout=30,
            )
            service = MusicService(AudioCache(root / "cache", max_bytes=1024 * 1024), max_audio_bytes=1024 * 1024)
            with patch("mobile.backend.music_service.music.download_audio", return_value=({}, source)):
                destination = service._create_audio("abcdefghijk", root)
            self.assertNotEqual(source, destination)
            self.assertGreater(destination.stat().st_size, 0)
            self.assertTrue(source.exists())

    def test_real_transcode_produces_aac_in_mp4(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "tone.wav"
            subprocess.run(
                [
                    shutil.which("ffmpeg"), "-nostdin", "-hide_banner", "-loglevel", "error",
                    "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-c:a", "pcm_s16le", str(source),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            destination = root / "tone.m4a"
            _convert_to_m4a(source, destination)
            self.assertGreater(destination.stat().st_size, 1000)
            probe = subprocess.run(
                [
                    shutil.which("ffprobe"), "-v", "error", "-select_streams", "a:0",
                    "-show_entries", "stream=codec_name,channels,sample_rate",
                    "-show_entries", "format=format_name", "-of", "json", str(destination),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            information = json.loads(probe.stdout)
            self.assertEqual(information["streams"][0]["codec_name"], "aac")
            self.assertIn("mp4", information["format"]["format_name"])


if __name__ == "__main__":
    unittest.main()
