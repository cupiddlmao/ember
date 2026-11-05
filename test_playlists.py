import json
from pathlib import Path
import tempfile
import unittest

import playlists


RECORD = {
    "id": "PL1234567890",
    "title": "Local playlist",
    "owner": "Example owner",
    "uri": "https://music.youtube.com/playlist?list=PL1234567890",
    "images": [{"url": "https://i.ytimg.com/vi/dm4tkSNKfFI/hqdefault.jpg", "width": 544}],
    "track_count": 12,
    "tracks": [{"uri": "https://music.youtube.com/watch?v=dm4tkSNKfFI"}],
    "stream": "https://rr1.googlevideo.com/audio",
}


class SavedPlaylistTests(unittest.TestCase):
    def test_clean_keeps_metadata_only_and_canonical_url(self):
        result = playlists.clean(RECORD)
        self.assertEqual(result["id"], "PL1234567890")
        self.assertEqual(result["uri"], "https://music.youtube.com/playlist?list=PL1234567890")
        self.assertNotIn("tracks", result)
        self.assertNotIn("stream", result)
        self.assertEqual(result["track_count"], 12)
        self.assertEqual(result["images"][0]["width"], 544)

    def test_saved_playlists_reload_and_replace_by_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlists.json"
            saved = playlists.save(path, [RECORD])
            self.assertEqual(len(saved), 1)
            self.assertEqual(playlists.load(path)[0]["title"], "Local playlist")
            raw = json.loads(path.read_text())
            self.assertEqual(raw["version"], 1)
            self.assertNotIn("tracks", raw["playlists"][0])
            changed = {**RECORD, "title": "Updated title"}
            saved = playlists.add(saved, changed)
            self.assertEqual([item["title"] for item in saved], ["Updated title"])
            self.assertEqual(playlists.remove(saved, "PL1234567890"), [])
            playlists.save(path, saved)
            self.assertEqual(playlists.load(path)[0]["title"], "Updated title")

    def test_invalid_or_duplicate_records_are_ignored(self):
        result = playlists.clean({**RECORD, "id": "not valid!"})
        self.assertIsNone(result)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlists.json"
            path.write_text(json.dumps({"playlists": [RECORD, RECORD, {"id": "bad"}]}))
            self.assertEqual(len(playlists.load(path)), 1)


if __name__ == "__main__":
    unittest.main()
