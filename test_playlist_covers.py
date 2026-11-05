import json
from pathlib import Path
import stat
import tempfile
import unittest

import playlist_covers


class PlaylistCoverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.index = root / "playlist-covers.json"
        self.covers = root / "covers"

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_and_private_file(self):
        playlist_id = "PL1234567890"
        covers = playlist_covers.write(playlist_id, b"png-data", path=self.index, covers_dir=self.covers)
        self.assertEqual(covers, {playlist_id: f"{playlist_id}.png"})
        self.assertEqual(playlist_covers.load(self.index, self.covers), covers)
        cover = playlist_covers.path_for(playlist_id, covers, self.covers)
        self.assertEqual(cover, self.covers / f"{playlist_id}.png")
        self.assertTrue(cover.is_file())
        self.assertEqual(stat.S_IMODE(cover.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.index.stat().st_mode), 0o600)

    def test_rejects_invalid_ids_and_empty_images(self):
        with self.assertRaises(ValueError):
            playlist_covers.write("../escape", b"image", path=self.index, covers_dir=self.covers)
        with self.assertRaises(ValueError):
            playlist_covers.write("PL1234567890", b"", path=self.index, covers_dir=self.covers)

    def test_load_ignores_path_traversal_entries(self):
        playlist_id = "PL1234567890"
        self.covers.mkdir()
        (self.covers / "other.png").write_bytes(b"image")
        self.index.write_text(json.dumps({"version": 1, "covers": {
            playlist_id: "../other.png",
            "PL0987654321": "PL0987654321.png",
        }}))
        self.assertEqual(playlist_covers.load(self.index, self.covers), {})

    def test_remove_deletes_only_the_selected_cover(self):
        first, second = "PL1234567890", "PL0987654321"
        playlist_covers.write(first, b"one", path=self.index, covers_dir=self.covers)
        playlist_covers.write(second, b"two", path=self.index, covers_dir=self.covers)
        covers = playlist_covers.remove(first, self.index, self.covers)
        self.assertNotIn(first, covers)
        self.assertIn(second, covers)
        self.assertFalse((self.covers / f"{first}.png").exists())
        self.assertTrue((self.covers / f"{second}.png").exists())


if __name__ == "__main__":
    unittest.main()
