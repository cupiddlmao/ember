"""Persistent, two-client playlist edits without provider/network access."""
import concurrent.futures
import sqlite3
import tempfile
from contextlib import closing
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from mobile.backend.library_store import LibraryError, LibraryStore, backup_sqlite_databases


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
        # ``with sqlite3.connect(...)`` only wraps a transaction; the handle
        # itself is left open, and on Windows that keeps the file locked.
        with closing(sqlite3.connect(destination)) as db, db:
            db.execute("PRAGMA user_version=99")
        with self.assertRaises(RuntimeError):
            LibraryStore(backup_dir)


class SchemaUpgradeTests(unittest.TestCase):
    """A schema bump must upgrade existing data instead of refusing to start."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "library"
        self.store = LibraryStore(self.root)
        self.id = uid()
        self.store.mutate("POST", None, {"id": self.id, "requestId": uid(),
                                          "name": "GG", "entries": [entry()]})

    def version(self, path=None):
        with closing(sqlite3.connect(path or self.root / "playlists.sqlite3")) as db:
            return db.execute("PRAGMA user_version").fetchone()[0]

    def test_upgrade_applies_ordered_steps_and_keeps_a_pre_upgrade_backup(self):
        def add_notes(db):
            db.execute("ALTER TABLE playlists ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        steps = {1: (add_notes,), 2: ("CREATE TABLE radio_seeds (id TEXT PRIMARY KEY)",)}
        with patch("mobile.backend.library_store.SCHEMA_VERSION", 3), \
                patch("mobile.backend.library_store._MIGRATIONS", steps):
            upgraded = LibraryStore(self.root)
            self.assertEqual(self.version(), 3)
            self.assertEqual(upgraded.get(self.id)["trackCount"], 1)
            backups = list(self.root.glob("playlists.v1.*.backup"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(self.version(backups[0]), 1)
            LibraryStore(self.root)
            self.assertEqual(len(list(self.root.glob("playlists.v*.backup"))), 1)
        with closing(sqlite3.connect(self.root / "playlists.sqlite3")) as db:
            self.assertIn("notes", [row[1] for row in db.execute("PRAGMA table_info(playlists)")])
            self.assertIn("radio_seeds", [row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")])

    def test_failed_step_rolls_back_and_a_fixed_retry_resumes(self):
        broken = {1: ("CREATE TABLE good (id TEXT)",), 2: ("CREATE TABLE bad (",)}
        with patch("mobile.backend.library_store.SCHEMA_VERSION", 3), \
                patch("mobile.backend.library_store._MIGRATIONS", broken):
            with self.assertRaises(RuntimeError) as error:
                LibraryStore(self.root)
        self.assertIn("version 2 to version 3", str(error.exception))
        self.assertEqual(self.version(), 2)
        self.assertEqual(self.store.get(self.id)["trackCount"], 1)
        with closing(sqlite3.connect(self.root / "playlists.sqlite3")) as db:
            tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("good", tables)
        self.assertNotIn("bad", tables)
        with patch("mobile.backend.library_store.SCHEMA_VERSION", 3), \
                patch("mobile.backend.library_store._MIGRATIONS",
                      {2: ("CREATE TABLE bad (id TEXT)",)}):
            LibraryStore(self.root)
        self.assertEqual(self.version(), 3)

    def test_missing_step_newer_and_unversioned_databases_are_refused(self):
        with patch("mobile.backend.library_store.SCHEMA_VERSION", 2), \
                patch("mobile.backend.library_store._MIGRATIONS", {}):
            with self.assertRaises(RuntimeError):
                LibraryStore(self.root)
        self.assertEqual(self.version(), 1)
        self.assertEqual(self.store.get(self.id)["trackCount"], 1)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        stranger = Path(temporary.name)
        with closing(sqlite3.connect(stranger / "playlists.sqlite3")) as db, db:
            db.execute("CREATE TABLE mystery (x)")
        with self.assertRaises(RuntimeError):
            LibraryStore(stranger)


class LibraryBackupTests(unittest.TestCase):
    def test_backup_covers_every_database_in_the_library(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "library"
        store = LibraryStore(root)
        playlist_id = uid()
        store.mutate("POST", None, {"id": playlist_id, "requestId": uid(),
                                     "name": "GG", "entries": [entry()]})
        with closing(sqlite3.connect(root / "lastfm.sqlite3")) as db, db:
            db.execute("CREATE TABLE account (id INTEGER PRIMARY KEY, session TEXT)")
            db.execute("INSERT INTO account VALUES (1, 'session-key')")
        (root / "playlists.v0.example.backup").write_text("not a database")
        destination = root / "backups" / "2026-11-23"
        written = backup_sqlite_databases(root, destination)
        self.assertEqual(sorted(path.name for path in written),
                         ["lastfm.sqlite3", "playlists.sqlite3"])
        with closing(sqlite3.connect(destination / "lastfm.sqlite3")) as db:
            self.assertEqual(db.execute("SELECT session FROM account").fetchone()[0], "session-key")
        self.assertEqual(LibraryStore(destination).get(playlist_id)["trackCount"], 1)
        with self.assertRaises(FileExistsError):
            backup_sqlite_databases(root, destination)


class LibraryConfigurationTests(unittest.TestCase):
    def test_library_must_be_outside_audio_cache(self):
        from mobile.backend.config import ConfigurationError, Settings
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            for library in (cache, cache / "library"):
                with self.assertRaises(ConfigurationError):
                    Settings(token="test", cache_dir=cache, library_dir=library)
