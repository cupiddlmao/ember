"""Eviction behaviour of the bounded audio cache.

These live in their own module because ``test_audio`` imports ``music_service``,
which needs yt-dlp and FFmpeg. Eviction is pure filesystem logic, so it stays
testable in an environment that has neither.

The layout exercised here is the one ``AudioCache`` actually writes: cache
entries are files named ``<videoId>.m4a`` directly in the cache root, a hit
refreshes their mtime through ``os.utime``, and in-flight work lives in
``.work-*`` directories and ``.<key>-<uuid>.m4a`` temporary files that must not
be counted as entries.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from mobile.backend.audio_cache import AudioCache


class EvictionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "cache"

    def _entry(self, name, size, modified):
        """Write one cache entry with an explicit mtime, standing in for age."""
        path = self.root / f"{name}.m4a"
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        path.write_bytes(b"a" * size)
        os.utime(path, (modified, modified))
        return path

    def _remaining(self):
        return sorted(path.name for path in self.root.glob("*.m4a") if path.is_file())

    def _total(self):
        return sum(path.stat().st_size for path in self.root.glob("*.m4a") if path.is_file())

    def test_oldest_entry_is_evicted_first(self):
        self._entry("oldest", 100, 1_000)
        self._entry("middle", 100, 2_000)
        self._entry("newest", 100, 3_000)

        AudioCache(self.root, max_bytes=250)

        # The freed 100 bytes came from the oldest entry, and nothing else moved.
        self.assertEqual(self._remaining(), ["middle.m4a", "newest.m4a"])
        self.assertLessEqual(self._total(), 250)

    def test_eviction_stops_as_soon_as_the_budget_fits(self):
        self._entry("first", 400, 1_000)
        self._entry("second", 300, 2_000)
        self._entry("third", 200, 3_000)

        AudioCache(self.root, max_bytes=500)

        # 900 bytes over a 500 byte budget needs exactly one eviction.
        self.assertEqual(self._remaining(), ["second.m4a", "third.m4a"])
        self.assertLessEqual(self._total(), 500)

    def test_eviction_order_is_deterministic_when_mtimes_tie(self):
        # Coarse timestamp granularity, or a cache restored from a backup, can
        # leave every entry with the same mtime. Eviction must then fall back on
        # a stable key instead of whatever order the filesystem happens to list.
        for name in ("alpha", "bravo", "charlie", "delta"):
            self._entry(name, 100, 5_000)

        AudioCache(self.root, max_bytes=200)

        self.assertEqual(self._remaining(), ["charlie.m4a", "delta.m4a"])
        self.assertLessEqual(self._total(), 200)

    def test_touched_entry_survives_a_later_eviction(self):
        # A hit refreshes mtime, which is how this cache records "in use". A
        # track being streamed must therefore outlive the tracks around it.
        self._entry("stale", 100, 1_000)
        self._entry("recent", 100, 2_000)
        cache = AudioCache(self.root, max_bytes=1_000)

        def never_called(work):
            raise AssertionError("a hit must not regenerate the entry")

        self.assertTrue(cache.get_or_create("stale", never_called).is_file())

        self._entry("newcomer", 100, 3_000)
        AudioCache(self.root, max_bytes=250)

        self.assertEqual(self._remaining(), ["newcomer.m4a", "stale.m4a"])

    @unittest.skipIf(os.name != "posix", "unlinking an open file is POSIX behaviour")
    def test_entry_still_being_read_survives_eviction_of_its_directory_entry(self):
        # Nothing pins an entry, but on POSIX an unlinked file stays readable
        # through the descriptor the streaming response already holds, so
        # eviction cannot truncate audio that is mid-playback.
        path = self._entry("playing", 100, 1_000)
        self._entry("newer", 100, 2_000)
        with path.open("rb") as stream:
            AudioCache(self.root, max_bytes=100)
            self.assertFalse(path.exists())
            self.assertEqual(stream.read(), b"a" * 100)

    def test_missing_directory_is_created(self):
        cache = AudioCache(self.root / "nested" / "deeper", max_bytes=100)

        self.assertTrue(cache.root.is_dir())
        self.assertEqual(cache.root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self._remaining(), [])

    def test_empty_directory_evicts_nothing(self):
        self.root.mkdir(parents=True, mode=0o700)

        AudioCache(self.root, max_bytes=100)

        self.assertEqual(self._remaining(), [])

    def test_directory_removed_after_construction_does_not_raise(self):
        cache = AudioCache(self.root, max_bytes=100)
        self.root.rmdir()

        cache._evict()

    def test_odd_filenames_are_evicted_by_size_not_by_interpretation(self):
        names = [
            "with space",
            "quote'and\"double",
            "semi;colon&amp",
            "newline\nname",
            "unicode-\u00fc\u00f1\u00ee",
            "$(touch pwned)",
            "back\\slash",
            "n" * 200,
        ]
        for index, name in enumerate(names):
            self._entry(name, 50, 1_000 + index)

        AudioCache(self.root, max_bytes=100)

        # 400 bytes over a 100 byte budget leaves exactly the two newest.
        self.assertEqual(self._remaining(), [f"{names[-2]}.m4a", f"{names[-1]}.m4a"])
        self.assertLessEqual(self._total(), 100)
        self.assertFalse((Path(self.temporary.name) / "pwned").exists())

    def test_directory_named_like_an_entry_is_ignored(self):
        self._entry("real", 50, 1_000)
        (self.root / "impostor.m4a").mkdir()

        AudioCache(self.root, max_bytes=100)

        self.assertEqual(self._remaining(), ["real.m4a"])
        self.assertTrue((self.root / "impostor.m4a").is_dir())

    def test_symlink_to_an_outside_file_is_never_followed_for_deletion(self):
        outside = Path(self.temporary.name) / "keep-me.bin"
        outside.write_bytes(b"precious")
        self.root.mkdir(parents=True, mode=0o700)
        (self.root / "outside.m4a").symlink_to(outside)
        self._entry("newer", 50, 2_000)

        AudioCache(self.root, max_bytes=50)

        self.assertTrue(outside.is_file())
        self.assertEqual(outside.read_bytes(), b"precious")

    def test_files_that_are_not_entries_are_left_alone(self):
        self._entry("entry", 50, 1_000)
        (self.root / ".work-inflight").mkdir()
        (self.root / ".work-inflight" / "partial.m4a").write_bytes(b"z" * 10)
        (self.root / "notes.txt").write_bytes(b"z" * 10)

        AudioCache(self.root, max_bytes=50)

        self.assertEqual(self._remaining(), ["entry.m4a"])
        self.assertTrue((self.root / ".work-inflight" / "partial.m4a").is_file())
        self.assertTrue((self.root / "notes.txt").is_file())

    def test_entry_larger_than_the_total_budget_is_refused_not_returned_stale(self):
        # A per-entry cap above the total budget made get_or_create hand back a
        # path it had just evicted, so the caller opened a missing file.
        cache = AudioCache(self.root, max_bytes=100, max_entry_bytes=1_000)

        def create(work):
            path = work / "audio.m4a"
            path.write_bytes(b"a" * 800)
            return path

        with self.assertRaises(RuntimeError):
            cache.get_or_create("abcdefghijk", create)
        self.assertEqual(self._remaining(), [])
        self.assertFalse(cache.path_for("abcdefghijk").exists())

    def test_single_entry_within_budget_stays_cached(self):
        cache = AudioCache(self.root, max_bytes=1_000)

        def create(work):
            path = work / "audio.m4a"
            path.write_bytes(b"a" * 600)
            return path

        path = cache.get_or_create("abcdefghijk", create)

        self.assertTrue(path.is_file())
        self.assertEqual(path.stat().st_size, 600)


if __name__ == "__main__":
    unittest.main()
