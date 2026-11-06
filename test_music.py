import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from music import cached_playlist, download_audio, music_link, normalize, resolve, search


class MusicTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        cache = patch('disk_cache.ROOT', Path(folder.name))
        cache.start()
        self.addCleanup(cache.stop)

    def test_links(self):
        self.assertEqual(music_link('https://music.youtube.com/watch?v=dm4tkSNKfFI&si=tracking'), ('dm4tkSNKfFI', ''))
        self.assertEqual(music_link('https://youtu.be/dm4tkSNKfFI')[0], 'dm4tkSNKfFI')
        for url in ('http://youtube.com/watch?v=dm4tkSNKfFI', 'https://youtube.com.evil.test/watch?v=dm4tkSNKfFI', 'https://user@youtube.com/watch?v=dm4tkSNKfFI', 'https://youtube.com/', 'https://youtube.com/watch?v=bad'):
            with self.assertRaises(ValueError): music_link(url)

    def test_metadata(self):
        track = normalize({'videoId': 'dm4tkSNKfFI', 'title': 'Awake', 'artists': [{'name': 'Tycho'}], 'duration': '4:44', 'thumbnails': [{'url': 'https://example.test/art'}]})
        self.assertEqual((track['name'], track['duration_ms'], track['source']), ('Awake', 284000, 'youtube'))
        self.assertEqual(track['artists'], [{'name': 'Tycho'}])
        self.assertEqual(normalize({'id': 'dm4tkSNKfFI', 'artists': ['Tycho']})['artists'], [{'name': 'Tycho'}])
        fallback = normalize({'videoId': 'dm4tkSNKfFI', 'title': 'Awake', 'artists': [{'name': 'Dance Gavin Dance - Topic'}]})
        self.assertEqual(fallback['artists'], [{'name': 'Dance Gavin Dance'}])
        self.assertEqual(fallback['album']['images'][0]['url'], 'https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg')

    def test_song_search_and_playlist_metadata(self):
        api = MagicMock()
        api.search.return_value = [{'videoId': 'dm4tkSNKfFI', 'title': 'Awake'}, {'videoId': None}]
        api.get_playlist.return_value = {
            'title': 'Night Drive',
            'author': {'name': 'Example owner'},
            'thumbnails': [{'url': 'https://i.ytimg.com/vi/dm4tkSNKfFI/hqdefault.jpg', 'width': 544}],
            'tracks': [
                {'videoId': 'dm4tkSNKfFI', 'title': 'One', 'duration': '3:00'},
                {'videoId': 'dm4tkSNKfFI', 'title': 'One', 'duration': '3:00'},
                {'videoId': None, 'title': 'Unavailable'},
                {'videoId': 'bad', 'title': 'Invalid'},
            ],
        }
        with patch('music.YTMusic', return_value=api):
            result = search('Tycho')
            self.assertEqual(result['kind'], 'search')
            self.assertEqual(len(result['tracks']), 1)
            playlist = search('https://music.youtube.com/playlist?list=PL1234567890')
            self.assertEqual(playlist['kind'], 'playlist')
            self.assertEqual(playlist['playlist']['title'], 'Night Drive')
            self.assertEqual(playlist['playlist']['owner'], 'Example owner')
            self.assertEqual(playlist['playlist']['uri'], 'https://music.youtube.com/playlist?list=PL1234567890')
            self.assertEqual(playlist['playlist']['track_count'], 2)
            self.assertEqual(playlist['unavailable'], 2)
            self.assertEqual([track['id'] for track in playlist['tracks']], ['dm4tkSNKfFI', 'dm4tkSNKfFI'])
            api.get_playlist.assert_called_once_with('PL1234567890', limit=None)

    def test_public_playlist_is_cached(self):
        api = MagicMock()
        api.get_playlist.return_value = {
            'title': 'Night Drive',
            'tracks': [{'videoId': 'dm4tkSNKfFI', 'title': 'One', 'duration': '3:00'}],
        }
        url = 'https://music.youtube.com/playlist?list=PL1234567890'
        with patch('music.YTMusic', return_value=api):
            search(url)
        cached = cached_playlist(url)
        self.assertEqual(cached['playlist']['title'], 'Night Drive')
        self.assertEqual([track['id'] for track in cached['tracks']], ['dm4tkSNKfFI'])

    def test_download_writes_audio_file_and_returns_metadata(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def extract_info(self, _url, download=False):
                assert download
                Path(self.options['outtmpl'].replace('%(ext)s', 'webm')).write_bytes(b'x' * 2048)
                return {'id': 'dm4tkSNKfFI', 'title': 'Awake', 'duration': 284, 'has_drm': False}
        with patch('music.yt_dlp.YoutubeDL', FakeYoutubeDL):
            resolved, path = download_audio({'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'}, folder.name)
        self.assertEqual(resolved['name'], 'Awake')
        self.assertEqual(path.suffix, '.webm')
        self.assertGreater(path.stat().st_size, 0)

    def test_stream_host_and_drm_boundary(self):
        client = MagicMock()
        client.__enter__.return_value = client
        data = {'id': 'dm4tkSNKfFI', 'title': 'Awake', 'url': 'https://audio.googlevideo.com/music', 'duration': 284}
        client.extract_info.return_value = data
        with patch('music.yt_dlp.YoutubeDL', return_value=client):
            self.assertEqual(resolve({'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'})[0]['name'], 'Awake')
            for changes in ({'url': 'https://evil.test/audio'}, {'has_drm': True}):
                client.extract_info.return_value = {**data, **changes}
                with self.assertRaises(RuntimeError): resolve({'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'})


if __name__ == '__main__': unittest.main()
