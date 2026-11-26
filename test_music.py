import unittest
import tempfile
from pathlib import Path
from pathlib import Path
from unittest.mock import patch, MagicMock

import yt_dlp
from yt_dlp.cookies import CookieLoadError

from music import AUDIO_FORMAT, _ytdlp_options, cached_playlist, download_audio, music_link, normalize, resolve, search


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

    def test_song_radio_uses_public_related_mix(self):
        api = MagicMock()
        api.get_watch_playlist.return_value = {'tracks': [
            {'videoId': 'dm4tkSNKfFI', 'title': 'Seed'},
            {'videoId': 'Ux_ZYXUemTU', 'title': 'Related'},
            {'videoId': None},
        ]}
        with patch('music.YTMusic', return_value=api), patch('music.resolve') as resolve:
            result = search('https://music.youtube.com/watch?v=dm4tkSNKfFI&list=RDAMVMdm4tkSNKfFI')
            self.assertEqual(result['kind'], 'radio')
            self.assertEqual(len(result['tracks']), 2)
            self.assertEqual(result['unavailable'], 1)
            api.get_watch_playlist.assert_called_once_with(videoId='dm4tkSNKfFI', radio=True, limit=25)
            resolve.assert_not_called()
            search('https://music.youtube.com/watch?v=dm4tkSNKfFI')
            resolve.assert_called_once()

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

    def test_ytdlp_options_pick_up_account_cookies_from_the_environment(self):
        with patch.dict('os.environ', {'EMBER_YOUTUBE_COOKIES': '/etc/ember/cookies.txt'}):
            self.assertEqual(_ytdlp_options()['cookiefile'], '/etc/ember/cookies.txt')
        with patch.dict('os.environ', {}, clear=True):
            self.assertNotIn('cookiefile', _ytdlp_options())
        with patch.dict('os.environ', {'EMBER_YOUTUBE_COOKIES': '   '}):
            self.assertNotIn('cookiefile', _ytdlp_options())

    def test_audio_format_prefers_audio_only_and_accepts_a_muxed_recording(self):
        selector = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}).build_format_selector(AUDIO_FORMAT)

        def pick(*formats):
            # The same context yt_dlp's own format selection builds.
            return [f['format_id'] for f in selector({
                'formats': list(formats), 'incomplete_formats': True, 'has_merged_format': True,
            })]

        audio_only = {'format_id': '251', 'acodec': 'opus', 'vcodec': 'none', 'protocol': 'https'}
        muxed = {'format_id': '18', 'acodec': 'mp4a.40.2', 'vcodec': 'avc1.42001E', 'protocol': 'https'}
        storyboard = {'format_id': 'sb0', 'acodec': 'none', 'vcodec': 'none', 'protocol': 'mhtml'}
        manifest = {'format_id': '96', 'acodec': 'mp4a.40.2', 'vcodec': 'avc1', 'protocol': 'm3u8_native'}
        self.assertEqual(pick(muxed, audio_only), ['251'])
        self.assertEqual(pick(muxed), ['18'])
        self.assertEqual(pick(storyboard, manifest), [])

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

    def test_download_audio_passes_and_enforces_the_size_cap(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        seen = {}
        class FakeYoutubeDL:
            def __init__(self, options):
                self.options = seen.update(options) or options
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False
            def extract_info(self, _url, download=False):
                Path(self.options['outtmpl'].replace('%(ext)s', 'webm')).write_bytes(b'x' * 2048)
                return {'id': 'dm4tkSNKfFI', 'title': 'Awake', 'duration': 284, 'has_drm': False}
        uri = {'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'}
        with patch('music.yt_dlp.YoutubeDL', FakeYoutubeDL):
            download_audio(uri, folder.name, max_bytes=4096)
            self.assertEqual(seen['max_filesize'], 4096)
            with self.assertRaises(RuntimeError):
                download_audio(uri, folder.name, max_bytes=1024)
        self.assertEqual(list(Path(folder.name).iterdir()), [])

    def test_a_gated_recording_streams_its_muxed_format_instead_of_an_hls_manifest(self):
        # resolve() accepts manifest.googlevideo.com as a stream host, so an
        # audio-only HLS manifest chosen here reaches the player and only fails
        # once playback starts, instead of streaming the muxed format it can play.
        selector = yt_dlp.YoutubeDL({'quiet': True, 'no_warnings': True}).build_format_selector(AUDIO_FORMAT)

        def pick(*formats):
            return [f['format_id'] for f in selector({
                'formats': list(formats), 'incomplete_formats': True, 'has_merged_format': True,
            })]

        hls_audio = {'format_id': '140-1', 'acodec': 'mp4a.40.2', 'vcodec': 'none', 'protocol': 'm3u8_native'}
        muxed = {'format_id': '18', 'acodec': 'mp4a.40.2', 'vcodec': 'avc1.42001E', 'protocol': 'https'}
        self.assertEqual(pick(hls_audio, muxed), ['18'])
        self.assertEqual(pick(hls_audio), [])

    def test_a_live_recording_with_text_in_place_of_a_duration_stays_in_the_results(self):
        # A live recording has no length; ytmusicapi writes "LIVE" where the
        # duration text would be. Parsing that as a number raised and the whole
        # recording was counted unavailable and dropped.
        live = {'videoId': 'dm4tkSNKfFI', 'title': 'Live at the Bowl', 'artists': [{'name': 'Tycho'}],
                'duration': 'LIVE', 'duration_seconds': None}
        self.assertEqual(normalize(live)['duration_ms'], 0)
        api = MagicMock()
        api.get_playlist.return_value = {'title': 'Night Drive', 'tracks': [live, {'videoId': 'Ux_ZYXUemTU', 'title': 'Encore'}]}
        with patch('music.YTMusic', return_value=api):
            result = search('https://music.youtube.com/playlist?list=PL1234567890')
        self.assertEqual([track['name'] for track in result['tracks']], ['Live at the Bowl', 'Encore'])
        self.assertEqual(result['unavailable'], 0)

    def test_an_unusable_cookie_file_is_a_classified_failure(self):
        # yt-dlp re-raises CookieLoadError out of extraction instead of wrapping
        # it in DownloadError, so catching DownloadError alone let it reach the
        # caller as a crash the bridge cannot describe.
        class BrokenCookies:
            def __init__(self, _options): pass
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def extract_info(self, *_args, **_kwargs):
                raise CookieLoadError('failed to load cookies')
        with patch('music.yt_dlp.YoutubeDL', BrokenCookies):
            with self.assertRaises(RuntimeError):
                resolve({'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'})

    def test_a_cookie_file_the_service_cannot_rewrite_does_not_discard_audio(self):
        # yt-dlp rewrites the cookies file when the session closes, so a
        # root-owned deployment path raised PermissionError after a stream had
        # already resolved, and the caller threw away playable audio.
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)

        class ReadOnlyCookies:
            def __init__(self, options): self.options = options
            def __enter__(self): return self
            def __exit__(self, *_):
                raise PermissionError(13, 'Permission denied', str(self.options.get('cookiefile')))
            def extract_info(self, _url, download=False):
                info = {'id': 'dm4tkSNKfFI', 'title': 'Awake', 'duration': 284, 'has_drm': False}
                if download:
                    Path(self.options['outtmpl'].replace('%(ext)s', 'webm')).write_bytes(b'x' * 2048)
                else:
                    info['url'] = 'https://audio.googlevideo.com/music'
                return info

        uri = {'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI'}
        with patch('music.yt_dlp.YoutubeDL', ReadOnlyCookies):
            track, stream = resolve(uri)
            self.assertEqual((track['name'], stream), ('Awake', 'https://audio.googlevideo.com/music'))
            self.assertEqual(download_audio(uri, folder.name)[1].suffix, '.webm')

        class UnreadableCookies(ReadOnlyCookies):
            def extract_info(self, *_args, **_kwargs):
                raise PermissionError(13, 'Permission denied')
        with patch('music.yt_dlp.YoutubeDL', UnreadableCookies):
            with self.assertRaises(RuntimeError):
                resolve(uri)

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
