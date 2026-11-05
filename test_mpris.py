from types import SimpleNamespace
import unittest

from gi.repository import GLib

from mpris import can_go_next, can_go_previous, can_play, can_seek, playback_status, track_path_for


class MprisTests(unittest.TestCase):
    def test_provider_ids_are_encoded_into_valid_object_paths(self):
        path = track_path_for("abc-def_ghi")
        component = path.rsplit("/", 1)[-1]
        self.assertTrue(component)
        self.assertTrue(all(character in "0123456789abcdef" for character in component))
        GLib.Variant("o", path)
        self.assertNotEqual(track_path_for("abc-def"), track_path_for("abc_def"))
        GLib.Variant("o", track_path_for("preview"))

    def test_capabilities_follow_queue_and_media_state(self):
        idle = SimpleNamespace(track=None, demo=False, pending_track=False, queue=[], queue_index=-1, repeat=False, shuffle=False, playing=False, duration=0)
        self.assertEqual(playback_status(idle), "Stopped")
        self.assertFalse(can_play(idle))
        self.assertFalse(can_seek(idle))
        self.assertFalse(can_go_next(idle))
        self.assertFalse(can_go_previous(idle))

        track = {"id": "dm4tkSNKfFI"}
        paused = SimpleNamespace(track=track, demo=False, pending_track=False, queue=[track, track, track], queue_index=1, repeat=False, shuffle=False, playing=False, duration=240)
        self.assertEqual(playback_status(paused), "Paused")
        self.assertTrue(can_play(paused))
        self.assertTrue(can_seek(paused))
        self.assertTrue(can_go_next(paused))
        self.assertTrue(can_go_previous(paused))

        at_end = SimpleNamespace(**{**paused.__dict__, "queue_index": 2})
        self.assertFalse(can_go_next(at_end))
        self.assertTrue(can_go_previous(at_end))
        repeated = SimpleNamespace(**{**at_end.__dict__, "repeat": True})
        self.assertTrue(can_go_next(repeated))
        shuffled = SimpleNamespace(**{**at_end.__dict__, "shuffle": True})
        self.assertTrue(can_go_next(shuffled))

        pending = SimpleNamespace(**{**paused.__dict__, "pending_track": True})
        self.assertEqual(playback_status(pending), "Stopped")
        self.assertFalse(can_play(pending))
        self.assertFalse(can_seek(pending))


if __name__ == "__main__":
    unittest.main()
