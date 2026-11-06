import copy
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import library_client as client
from mobile.backend.config import Settings
from mobile.backend.http_api import create_server
from mobile.backend.tests.test_http_api import FakeService, FakePlaylists, FakeLyrics, TOKEN

TRACK = {"id": "dm4tkSNKfFI", "title": "Awake", "artist": "Tycho", "album": "Awake", "durationMs": 284000, "artworkUrl": None, "source": "youtube"}

class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {"XDG_DATA_HOME": str(root / "desktop")})
        self.env.start(); self.addCleanup(self.env.stop)
        settings = Settings(token=TOKEN, cache_dir=root / "cache", library_dir=root / "library", rate_limit_per_minute=1000)
        self.server = create_server(settings, service=FakeService(root), address=("127.0.0.1", 0), playlists=FakePlaylists(), lyrics=FakeLyrics())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start(); self.addCleanup(self.stop)
        self.address = "http://127.0.0.1:" + str(self.server.server_address[1]) + "/ember"
        client.private_save(client.root() / "connection.json", {"server": self.address, "key": TOKEN})
    def stop(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(3)
    def call(self, operation, **arguments):
        return client.execute({"operation": operation, **arguments})
    def create(self, name="GG"):
        state = self.call("create", name=name)
        self.assertTrue(state["acknowledged"], state)
        return state["detail"]
    def test_multiple_playlists_duplicate_occurrences_edit_and_delete(self):
        first = self.create(); self.create("Second")
        state = self.call("add", id=first["id"], track=TRACK)
        self.assertTrue(state["acknowledged"])
        state = self.call("add", id=first["id"], track=TRACK)
        detail = state["detail"]; entries = detail["entries"]
        self.assertEqual(len(state["playlists"]), 2)
        self.assertNotEqual(entries[0]["id"], entries[1]["id"])
        state = self.call("move", id=detail["id"], revision=detail["revision"], entry=entries[1]["id"], delta=-1)
        detail = state["detail"]
        self.assertEqual([e["id"] for e in detail["entries"]], [entries[1]["id"], entries[0]["id"]])
        state = self.call("rename", id=detail["id"], revision=detail["revision"], name="Renamed")
        detail = state["detail"]; self.assertEqual(detail["name"], "Renamed"); self.assertEqual(len(detail["entries"]), 2)
        state = self.call("remove", id=detail["id"], revision=detail["revision"], entry=entries[0]["id"])
        detail = state["detail"]; self.assertEqual(detail["entries"][0]["id"], entries[1]["id"])
        state = self.call("delete", id=detail["id"], revision=detail["revision"])
        self.assertTrue(state["acknowledged"]); self.assertEqual(len(state["playlists"]), 1); self.assertIsNone(state["detail"])
    def test_conflict_does_not_overwrite_other_device(self):
        detail = self.create()
        state = self.call("rename", id=detail["id"], revision=detail["revision"], name="Phone edit")
        state = self.call("rename", id=detail["id"], revision=detail["revision"], name="Stale PC edit")
        self.assertFalse(state["acknowledged"]); self.assertFalse(state["pending"])
        self.assertEqual(state["detail"]["name"], "Phone edit")
        self.assertIn("another device", state["error"])
    def test_committed_timeout_retries_identical_body_after_relaunch(self):
        detail = self.create()
        original = client.request; sent = []
        def ambiguous(server, key, method, suffix="", body=None):
            result = original(server, key, method, suffix, body)
            if method == "PUT":
                sent.append(copy.deepcopy(body)); raise TimeoutError()
            return result
        with patch.object(client, "request", side_effect=ambiguous):
            state = self.call("add", id=detail["id"], track=TRACK)
        self.assertTrue(state["pending"]); self.assertFalse(state["acknowledged"])
        # A new execute reads pending state from disk, not an in-memory object.
        def retry(server, key, method, suffix="", body=None):
            if method == "PUT": self.assertEqual(body, sent[0])
            return original(server, key, method, suffix, body)
        with patch.object(client, "request", side_effect=retry): state = self.call("retry")
        self.assertTrue(state["acknowledged"]); self.assertFalse(state["pending"])
        self.assertEqual(len(state["detail"]["entries"]), 1)
        self.assertNotIn(TOKEN, json.dumps(state))
    def test_offline_cache_and_unreadable_pending_guard(self):
        detail = self.create()
        with patch.object(client, "request", side_effect=OSError()):
            state = self.call("refresh")
            self.assertFalse(state["online"]); self.assertEqual(state["detail"]["id"], detail["id"])
            state = self.call("create", name="Offline")
            self.assertFalse(state["acknowledged"]); self.assertFalse(state["pending"])
        path = next(p for p in client.root().glob("*.json") if p.name != "connection.json")
        path.write_text("{broken")
        with patch.object(client, "request") as request:
            state = self.call("create", name="Blocked")
            request.assert_not_called()
        self.assertIn("cannot be read", state["error"])
        self.assertEqual(path.read_text(), "{broken")

    def test_unreadable_connection_file_is_preserved(self):
        connection = client.root() / "connection.json"
        connection.write_text("{broken")
        with patch.object(client, "request") as request:
            state = self.call("refresh")
            request.assert_not_called()
        self.assertIn("connection file cannot be read", state["error"])
        self.assertEqual(connection.read_text(), "{broken")

    def test_rejected_save_stays_pending_until_explicit_discard(self):
        detail = self.create()
        original = client.request
        def reject(server, key, method, suffix="", body=None):
            if method == "PUT":
                raise client.Rejected(409)
            return original(server, key, method, suffix, body)
        with patch.object(client, "request", side_effect=reject):
            state = self.call("rename", id=detail["id"], revision=detail["revision"], name="Rejected")
        self.assertFalse(state["acknowledged"])
        self.assertTrue(state["pending"])
        self.assertIn("Discard pending save", state["error"])
        path = next(p for p in client.root().glob("*.json") if p.name != "connection.json")
        self.assertIsNotNone(json.loads(path.read_text())["pending"])
        with patch.object(client, "request") as request:
            blocked = self.call("create", name="Blocked")
            request.assert_not_called()
        self.assertTrue(blocked["pending"])
        state = self.call("discard")
        self.assertFalse(state["pending"])
        self.assertFalse(json.loads(path.read_text()).get("pending"))
        self.assertTrue(state["online"])

    def test_discard_requires_a_pending_save(self):
        self.create()
        state = self.call("discard")
        self.assertFalse(state["pending"])
        self.assertTrue(state["error"])
    def test_connection_rules_and_server_cache_isolation(self):
        self.create()
        client.private_save(client.root() / "connection.json", {"server": "http://example.com", "key": TOKEN})
        with self.assertRaises(ValueError): client.connection()
        client.private_save(client.root() / "connection.json", {"server": "https://elsewhere.invalid/ember", "key": TOKEN})
        with patch.object(client, "request", side_effect=OSError()): state = self.call("refresh")
        self.assertEqual(state["playlists"], []); self.assertIsNone(state["detail"])
    def test_private_cache_failure_prevents_writes(self):
        self.create()
        original = client.request; writes = []
        def request(server, key, method, suffix="", body=None):
            if method != "GET": writes.append(method)
            return original(server, key, method, suffix, body)
        with patch.object(client, "private_save", side_effect=OSError()), patch.object(client, "request", side_effect=request):
            state = self.call("create", name="Not sent")
        self.assertEqual(writes, []); self.assertFalse(state["pending"])

if __name__ == "__main__": unittest.main()
