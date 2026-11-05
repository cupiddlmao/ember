import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from audio_cache import AudioStore


TRACK = {
    'source': 'youtube', 'type': 'track', 'id': 'dm4tkSNKfFI',
    'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI',
    'name': 'Awake', 'artists': [{'name': 'Tycho'}],
    'album': {'name': 'Awake', 'images': []}, 'duration_ms': 284000,
}


class AudioStoreTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.store = AudioStore(
            cache_root=self.root / 'cache',
            download_root=self.root / 'downloads',
            index_path=self.root / 'audio-index.sqlite3',
            limit=250,
        )
        self.download = patch('audio_cache.music.download_audio', side_effect=self.fake_download)
        self.download_mock = self.download.start()
        self.addCleanup(self.download.stop)
        self.addCleanup(self.folder.cleanup)

    @staticmethod
    def fake_download(track, folder, progress=None):
        folder = Path(folder)
        path = folder / (track['id'] + '.webm')
        path.write_bytes(b'x' * 100)
        return {**track, 'name': track['name'] + ' (resolved)'}, path

    def track(self, video, name='Track'):
        return {**TRACK, 'id': video, 'name': name, 'uri': 'https://music.youtube.com/watch?v=' + video}

    def test_explicit_download_is_private_persistent_and_removable(self):
        result = self.store.download(TRACK, pinned=True)
        self.assertTrue(result['pinned'])
        self.assertTrue(result['path'].is_relative_to(self.store.download_root))
        self.assertEqual(result['path'].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.store.lookup(TRACK)['path'], result['path'])
        self.assertEqual(self.store.downloaded_tracks()[0]['name'], 'Awake (resolved)')
        self.assertEqual(self.store.stats()['pinned_count'], 1)

        self.store.clear_auto()
        self.assertIsNotNone(self.store.lookup(TRACK))
        self.assertTrue(self.store.remove(TRACK['id']))
        self.assertIsNone(self.store.lookup(TRACK))

    def test_automatic_cache_is_bounded_and_lru_evicts_oldest(self):
        first = self.track('aaaaaaaaaaa', 'First')
        second = self.track('bbbbbbbbbbb', 'Second')
        third = self.track('ccccccccccc', 'Third')
        self.store.download(first)
        self.store.download(second)
        time.sleep(0.01)
        self.store.lookup(first)
        self.store.download(third)
        self.assertIsNotNone(self.store.lookup(first))
        self.assertIsNone(self.store.lookup(second))
        self.assertIsNotNone(self.store.lookup(third))
        self.assertLessEqual(self.store.stats()['auto_bytes'], 250)

    def test_automatic_copy_can_be_promoted_without_redownloading(self):
        cached = self.store.download(TRACK)
        self.assertFalse(cached['pinned'])
        self.assertTrue(cached['path'].is_relative_to(self.store.cache_root))
        pinned = self.store.pin(TRACK['id'])
        self.assertTrue(pinned['pinned'])
        self.assertTrue(pinned['path'].is_relative_to(self.store.download_root))
        self.store.clear_auto()
        self.assertEqual(self.store.lookup(TRACK)['path'], pinned['path'])
        self.assertEqual(self.download_mock.call_count, 1)

    def test_missing_file_is_pruned(self):
        result = self.store.download(TRACK, pinned=True)
        result['path'].unlink()
        self.assertIsNone(self.store.entry(TRACK['id']))
        self.assertEqual(self.store.stats()['pinned_count'], 0)

    def test_cleanup_removes_abandoned_temporary_directories(self):
        temp = self.store.cache_root / 'tmp' / 'abandoned'
        temp.mkdir(parents=True)
        (temp / 'partial.part').write_bytes(b'partial')
        self.store.cleanup()
        self.assertFalse(temp.exists())

    def test_cleanup_keeps_valid_cached_rows(self):
        self.store.download(TRACK, pinned=True)
        self.store.cleanup()
        self.assertIsNotNone(self.store.lookup(TRACK))


if __name__ == '__main__':
    unittest.main()
