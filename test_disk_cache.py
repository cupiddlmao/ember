import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import disk_cache as cache
import music


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name) / 'cache'
        self.patch = patch.object(cache, 'ROOT', self.root)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()

    def test_persistent_private_lru_and_bound(self):
        with patch.dict(cache.LIMITS, {'artwork': 10}):
            cache.put('artwork', 'first', b'12345', cache.generation())
            cache.put('artwork', 'second', b'67890', cache.generation())
            os.utime(cache._path('artwork', 'first'), (1, 1))
            self.assertEqual(cache.get('artwork', 'first'), b'12345')
            os.utime(cache._path('artwork', 'second'), (1, 1))
            cache.put('artwork', 'third', b'abcde', cache.generation())
            self.assertIsNone(cache.get('artwork', 'second'))
            self.assertEqual(cache.get('artwork', 'first'), b'12345')
            files = list((self.root/'artwork').glob('*.cache'))
            self.assertLessEqual(sum(p.stat().st_size for p in files), 10)
            self.assertTrue(all(p.stat().st_mode & 0o777 == 0o600 for p in files))
            cache.put('artwork', 'oversized', b'x'*11, cache.generation())
            self.assertIsNone(cache.get('artwork', 'oversized'))

    def test_corrupt_expired_and_cleared_entries(self):
        cache.put('metadata', 'broken', b'invalid json', cache.generation())
        self.assertIsNone(cache.get_metadata('broken'))
        cache.put('metadata', 'old', json.dumps({'saved_at': 1, 'data': ['old']}).encode(), cache.generation())
        self.assertIsNone(cache.get_metadata('old'))
        cache.put_metadata('fresh', {'tracks': []}, cache.generation())
        self.assertEqual(cache.get_metadata('fresh'), {'tracks': []})
        epoch = cache.generation()
        unrelated = self.root.parent/'likes.json'
        unrelated.write_text('keep')
        saved = self.root.parent/'youtube-playlists.json'
        saved.write_text('keep saved playlists')
        cache.clear()
        cache.put_metadata('late-worker', [], epoch)
        self.assertIsNone(cache.get_metadata('fresh'))
        self.assertIsNone(cache.get_metadata('late-worker'))
        self.assertEqual(unrelated.read_text(), 'keep')
        self.assertEqual(saved.read_text(), 'keep saved playlists')

    def test_accounts_are_isolated_and_missing_login_has_no_cache(self):
        auth = self.root.parent/'login.json'
        auth.write_text(json.dumps({'refresh_token': 'first-account'}))
        key = music._cache_key('playlist', auth)
        cache.put_metadata(key, {'title': 'private'}, cache.generation())
        auth.write_text(json.dumps({'refresh_token': 'second-account'}))
        self.assertIsNone(music.cached_playlist('playlist', auth))
        auth.unlink()
        self.assertIsNone(music.cached_playlist('playlist', auth))
        self.assertNotIn('first-account', key)

    def test_playlist_cache_survives_refresh_failure(self):
        result = {'kind': 'playlist', 'tracks': [], 'playlist': {'id': 'PL1234567890'}}
        with patch('music._search', return_value=result):
            music.search('https://music.youtube.com/playlist?list=PL1234567890')
        with patch('music._search', side_effect=RuntimeError('offline')):
            with self.assertRaises(RuntimeError):
                music.search('https://music.youtube.com/playlist?list=PL1234567890')
        self.assertEqual(music.cached_playlist('https://music.youtube.com/playlist?list=PL1234567890'), result)

    def test_artwork_worker_reuses_resized_image_without_network(self):
        from types import SimpleNamespace
        import threading
        from unittest.mock import MagicMock
        from app import Ember, GdkPixbuf, Gio, GLib
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 256, 256)
        pixbuf.fill(0xff0000ff)
        _, raw = pixbuf.save_to_bufferv('png', [], [])
        key = ('https://i.ytimg.com/example.jpg', 80)
        fake = SimpleNamespace(art_lock=threading.RLock(), art_waiters={key: [(None, None)]}, art_queued={key}, art_queue=MagicMock(), art_delivered=MagicMock())
        with patch('app.download_artwork', return_value=bytes(raw)) as download, patch('app.GLib.idle_add'):
            for _ in range(2):
                fake.art_queue.get.side_effect = [(1, 0, key), StopIteration]
                with self.assertRaises(StopIteration):
                    Ember.art_worker(fake)
            self.assertEqual(download.call_count, 1)
        resized = cache.get('artwork', f'{key[0]}:{key[1]}')
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(resized))
        image = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        self.assertEqual((image.get_width(), image.get_height()), (80, 80))
