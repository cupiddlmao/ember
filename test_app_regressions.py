from pathlib import Path
from types import SimpleNamespace
import threading
import queue
import unittest
from unittest.mock import MagicMock, patch

from app import Ember
from mpris import Mpris


class AppRegressions(unittest.TestCase):
    def test_google_login_is_nonmodal_reused_and_cancelled_on_close(self):
        fake = SimpleNamespace(google_login_window=None)
        with patch('app.Gtk.Window') as window, patch('app.Gtk.Box'), patch('app.Gtk.Entry'), patch('app.Gtk.PasswordEntry'), patch('app.Gtk.LinkButton'), patch('app.label'), patch('app.button'), patch('app.load_private', return_value={}):
            Ember.google_login(fake)
            self.assertFalse(window.call_args.kwargs['modal'])
            self.assertNotIn('transient_for', window.call_args.kwargs)
            Ember.google_login(fake)
            self.assertEqual(window.call_count, 1)
            close = window.return_value.connect.call_args.args[1]
            self.assertFalse(close())
            self.assertTrue(fake.google_login_cancel.is_set())
            self.assertIsNone(fake.google_login_window)

    def test_current_cover_precedes_row_downloads(self):
        fake = SimpleNamespace(art_lock=threading.RLock(), art_cache={}, art_waiters={}, art_queued=set(), art_sequence=0, art_queue=queue.PriorityQueue())
        Ember.load_artwork(fake, 'https://i.ytimg.com/row.jpg', lambda t: None, scope=object())
        Ember.load_artwork(fake, 'https://i.ytimg.com/current.jpg', lambda t: None)
        self.assertEqual(fake.art_queue.get()[2][0], 'https://i.ytimg.com/current.jpg')

    def test_cached_cover_applies_before_row_is_parented(self):
        widget = MagicMock()
        widget.get_root.return_value = None
        texture = object()
        Ember.art_callback(SimpleNamespace(), widget)(texture)
        widget.set_texture.assert_called_once_with(texture)

    def test_retiring_tracks_preserves_saved_covers(self):
        tracks, saved = object(), object()
        fake = SimpleNamespace(art_lock=threading.RLock(), art_waiters={('url', 80): [(None, tracks), (None, saved)]})
        Ember.retire_art_scope(fake, tracks)
        self.assertEqual(fake.art_waiters[('url', 80)], [(None, saved)])

    def test_worker_skips_abandoned_download(self):
        key = ('https://i.ytimg.com/old.jpg', 80)
        fake = SimpleNamespace(art_lock=threading.RLock(), art_waiters={}, art_queued={key}, art_queue=MagicMock())
        fake.art_queue.get.side_effect = [(1, 0, key), StopIteration]
        with patch('app.download_artwork') as download:
            with self.assertRaises(StopIteration):
                Ember.art_worker(fake)
            download.assert_not_called()
        self.assertNotIn(key, fake.art_queued)

    def test_cached_track_bypasses_the_remote_resolver(self):
        track = {'id': 'dm4tkSNKfFI', 'uri': 'https://music.youtube.com/watch?v=dm4tkSNKfFI',
                 'name': 'Awake', 'artists': [{'name': 'Tycho'}], 'duration_ms': 284000,
                 'album': {'images': []}}
        fake = SimpleNamespace(
            send=MagicMock(), demo=False, pending_track=False, playing=False, playback_origin=None,
            track=None, requests=MagicMock(), position=0, duration=0, set_metadata=MagicMock(),
            play_button=MagicMock(), update_playing_row=MagicMock(), update_download_button=MagicMock(),
            render_lyrics=MagicMock(), current_art_url=None, start_audio=MagicMock(),
            load_track_lyrics=MagicMock(), load_artwork=MagicMock(), lyrics_backdrop=MagicMock(), status=MagicMock(),
            player_art=SimpleNamespace(target=112, clear_art=MagicMock()),
            lyrics_art=SimpleNamespace(target=128, clear_art=MagicMock()),
        )
        fake.requests.start_track.return_value = (1, 2)
        fake.requests.lyrics_current.return_value = True
        cached = {'path': Path('/tmp/ember-track.webm'), 'pinned': True, 'video_id': track['id']}
        with patch('app.audio_store.lookup', return_value=cached), patch('app.music_resolve') as resolve:
            Ember.play_track(fake, track)
        resolve.assert_not_called()
        fake.start_audio.assert_called_once_with(track, cached['path'].as_uri(), 'download')

    def test_playlist_cover_choice_saves_scaled_png(self):
        playlist_id = "PL1234567890"
        fake = SimpleNamespace(
            playlist_cover_dialog=object(),
            playlist_covers={},
            playlist_meta={"id": playlist_id},
            visible_tracks=[],
            playlist_unavailable=0,
            refresh_playlist_cover=MagicMock(),
        )
        dialog = MagicMock()
        selected = MagicMock()
        selected.get_path.return_value = "/tmp/cover.jpg"
        dialog.open_finish.return_value = selected
        pixbuf = MagicMock()
        pixbuf.save_to_bufferv.return_value = (True, b"png")
        with patch('app.GdkPixbuf.Pixbuf.new_from_file_at_scale', return_value=pixbuf) as scale, \
             patch('app.playlist_covers.write', return_value={playlist_id: f"{playlist_id}.png"}) as write:
            Ember.playlist_cover_chosen(fake, dialog, None, playlist_id)
        scale.assert_called_once_with("/tmp/cover.jpg", 512, 512, True)
        write.assert_called_once_with(playlist_id, b"png")
        self.assertIsNone(fake.playlist_cover_dialog)
        fake.refresh_playlist_cover.assert_called_once_with(playlist_id)

    def test_lastfm_track_fields(self):
        track = {
            "name": "Awake",
            "artists": [{"name": "Tycho"}],
            "album": {"name": "Awake"},
            "duration_ms": 284000,
        }
        self.assertEqual(
            Ember.lastfm_track_fields(SimpleNamespace(), track),
            {"artist": "Tycho", "track": "Awake", "album": "Awake", "duration": 284},
        )
        self.assertIsNone(Ember.lastfm_track_fields(SimpleNamespace(), {"name": "", "artists": []}))

    def test_root_media_capabilities(self):
        media = object.__new__(Mpris)
        media.app = SimpleNamespace()
        for name in ('CanRaise', 'CanQuit'):
            self.assertTrue(media.get(None, None, None, None, name).unpack())
