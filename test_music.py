import unittest
from unittest.mock import patch, MagicMock

from music import music_link, normalize, resolve, search


class MusicTests(unittest.TestCase):
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

    def test_song_search_and_playlist(self):
        api = MagicMock()
        api.search.return_value = [{'videoId': 'dm4tkSNKfFI', 'title': 'Awake'}, {'videoId': None}]
        api.get_playlist.return_value = {'tracks': api.search.return_value}
        with patch('music.YTMusic', return_value=api):
            self.assertEqual(len(search('Tycho')), 1)
            api.search.assert_called_once_with('Tycho', filter='songs', limit=20)
            self.assertEqual(len(search('https://music.youtube.com/playlist?list=PL1234567890')), 1)
            api.get_playlist.assert_called_once_with('PL1234567890', limit=None)

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
