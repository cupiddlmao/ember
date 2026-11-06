import unittest

from playlist_search import filter_tracks


TRACKS = [
    {"id": "aaaaaaaaaaa", "name": "Awake", "artists": [{"name": "Tycho"}], "album": {"name": "Awake"}},
    {"id": "bbbbbbbbbbb", "name": "Daydream", "artists": [{"name": "Tycho"}], "album": {"name": "Dive"}},
    {"id": "aaaaaaaaaaa", "name": "Awake", "artists": [{"name": "Tycho"}], "album": {"name": "Awake"}},
]


class PlaylistSearchTests(unittest.TestCase):
    def test_filters_title_artist_and_album_case_insensitively(self):
        self.assertEqual([index for index, _track in filter_tracks(TRACKS, "awake")], [0, 2])
        self.assertEqual([index for index, _track in filter_tracks(TRACKS, "TYCHO")], [0, 1, 2])
        self.assertEqual([index for index, _track in filter_tracks(TRACKS, "dive")], [1])
        self.assertEqual([index for index, _track in filter_tracks(TRACKS, "nothing")], [])

    def test_empty_query_restores_all_and_keeps_duplicate_occurrences(self):
        matches = filter_tracks(TRACKS, "  ")
        self.assertEqual([index for index, _track in matches], [0, 1, 2])
        self.assertEqual([index for index, _track in filter_tracks(TRACKS, "awake")], [0, 2])
        self.assertEqual(len(TRACKS), 3)

    def test_filter_does_not_mutate_input(self):
        original = [dict(track) for track in TRACKS]
        filter_tracks(TRACKS, "tycho")
        self.assertEqual(TRACKS, original)


if __name__ == "__main__":
    unittest.main()
