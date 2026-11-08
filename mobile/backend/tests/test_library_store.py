"""Persistent, two-client playlist edits without provider/network access."""
import concurrent.futures
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mobile.backend.library_store import LibraryError, LibraryStore


def uid():
    return str(uuid.uuid4())


def entry():
    return {"id": uid(), "track": {"id": "dQw4w9WgXcQ", "title": "Song", "artist": "Artist",
                                  "album": "Album", "durationMs": 1000,
                                  "artworkUrl": "https://i.ytimg.com/vi/dQw4w9WgXcQ/default.jpg"}}


class LibraryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = LibraryStore(self.root / "library")
        self.id = uid()

    def create(self, entries=None):
        self.create_body = {"id": self.id, "requestId": uid(), "name": "GG", "entries": entries or []}
        return self.store.mutate("POST", None, self.create_body)

    def edit(self, entries, revision=1, name="GG"):
        return {"requestId": uid(), "expectedRevision": revision, "name": name, "entries": entries}

    def test_duplicates_reorder_remove_and_restart(self):
        a, b = entry(), entry()
        self.assertEqual(self.create([a, b])[0], 201)
        saved = self.store.get(self.id)
        self.assertEqual([x["id"] for x in saved["entries"]], [a["id"], b["id"]])
        self.store.mutate("PUT", self.id, self.edit([b, a], name="Renamed"))
        other = LibraryStore(self.root / "library")
        self.assertEqual([x["id"] for x in other.get(self.id)["entries"]], [b["id"], a["id"]])
        other.mutate("PUT", self.id, self.edit([a], 2, "Renamed"))
        self.assertEqual(self.store.get(self.id)["trackCount"], 1)
        self.assertEqual(self.store.list()["playlists"][0]["revision"], 3)

    def test_retries_do_not_duplicate_or_revert_later_changes(self):
        a = entry()
        result = self.create([a])
        self.assertEqual(self.store.mutate("POST", None, self.create_body), result)
        body = self.edit([a, entry()])
        result = self.store.mutate("PUT", self.id, body)
        self.assertEqual(self.store.mutate("PUT", self.id, body), result)
        self.store.mutate("PUT", self.id, self.edit([], 2))
        self.assertEqual(self.store.mutate("PUT", self.id, body), result)
        self.assertEqual(self.store.get(self.id)["revision"], 3)
        self.assertEqual(self.store.get(self.id)["entries"], [])
        with self.assertRaises(LibraryError) as conflict:
            self.store.mutate("PUT", self.id, {**body, "name": "Different"})
        self.assertEqual(conflict.exception.code, "request_conflict")

    def test_two_clients_can_not_overwrite_each_other(self):
        self.create()
        other = LibraryStore(self.root / "library")
        bodies = [self.edit([entry()], name=name) for name in ("PC", "Phone")]
        def save(pair):
            store, body = pair
            try:
                return store.mutate("PUT", self.id, body)[0]
            except LibraryError as error:
                return error.status
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            statuses = list(pool.map(save, zip((self.store, other), bodies)))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(self.store.get(self.id)["revision"], 2)

    def test_delete_retries_and_never_reuses_deleted_id(self):
        self.create([entry()])
        body = {"requestId": uid(), "expectedRevision": 1}
        result = self.store.mutate("DELETE", self.id, body)
        self.assertEqual(self.store.mutate("DELETE", self.id, body), result)
        self.assertEqual(self.store.list(), {"playlists": []})
        with self.assertRaises(LibraryError):
            self.store.get(self.id)
        self.create_body["requestId"] = uid()
        with self.assertRaises(LibraryError) as conflict:
            self.store.mutate("POST", None, self.create_body)
        self.assertEqual(conflict.exception.status, 409)

    def test_validation_leaves_previous_snapshot_unchanged(self):
        a = entry()
        self.create([a])
        for entries in ([a, a], [{**a, "track": {**a["track"], "stream_url": "secret"}}],
                        [{**a, "track": {**a["track"], "artworkUrl": "https://evil.test/a"}}],
                        [{**a, "track": {**a["track"], "durationMs": True}}]):
            with self.assertRaises(LibraryError):
                self.store.mutate("PUT", self.id, self.edit(entries))
        self.assertEqual(self.store.get(self.id)["revision"], 1)
        self.assertEqual(self.store.get(self.id)["trackCount"], 1)

    def test_quota_and_expired_retry_are_safe(self):
        self.create()
        with patch("mobile.backend.library_store.MAX_PLAYLISTS", 1):
            with self.assertRaises(LibraryError):
                self.store.mutate("POST", None, {**self.create_body, "id": uid(), "requestId": uid()})
        old = self.edit([])
        with patch("mobile.backend.library_store.MAX_RECEIPTS", 1):
            self.store.mutate("PUT", self.id, old)
            self.store.mutate("PUT", self.id, self.edit([], 2))
        with self.assertRaises(LibraryError) as conflict:
            self.store.mutate("PUT", self.id, old)
        self.assertEqual(conflict.exception.status, 409)
        self.assertEqual(self.store.get(self.id)["revision"], 3)

    def test_consistent_backup_and_schema_guard(self):
        self.create([entry()])
        backup_dir = self.root / "restored"
        backup_dir.mkdir()
        destination = backup_dir / "playlists.sqlite3"
        self.store.backup(destination)
        restored = LibraryStore(backup_dir)
        self.assertEqual(restored.get(self.id), self.store.get(self.id))
        with self.assertRaises(FileExistsError):
            self.store.backup(destination)
        import sqlite3
        with sqlite3.connect(destination) as db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(RuntimeError):
            LibraryStore(backup_dir)


class LibraryConfigurationTests(unittest.TestCase):
    def test_library_must_be_outside_audio_cache(self):
        from mobile.backend.config import ConfigurationError, Settings
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            for library in (cache, cache / "library"):
                with self.assertRaises(ConfigurationError):
                    Settings(token="test", cache_dir=cache, library_dir=library)
