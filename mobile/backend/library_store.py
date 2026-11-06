"""Ember-owned playlists. No provider credentials or network calls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

MAX_PLAYLISTS = 200
MAX_PLAYLIST_IDS = 10000  # Includes deleted IDs, which must never be reused.
MAX_ENTRIES = 5000
MAX_RECEIPTS = 1000
TRACK_FIELDS = {"id", "title", "artist", "album", "durationMs", "artworkUrl", "source"}
ARTWORK_HOSTS = {"i.ytimg.com", "img.youtube.com", "yt3.ggpht.com", "yt3.googleusercontent.com"}
ARTWORK_HOSTS.update(f"lh{i}.googleusercontent.com" for i in range(3, 7))


class LibraryError(ValueError):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code = status, code


def invalid(message="Invalid playlist request."):
    return LibraryError(400, "invalid_library", message)


def identifier(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise invalid("IDs must be canonical UUID strings.") from None
    return value


def text(value, limit, empty=False):
    if not isinstance(value, str):
        raise invalid("Playlist names and track metadata must be text.")
    value = value.strip()
    if (not value and not empty) or len(value) > limit or any(
        unicodedata.category(ch) in ("Cc", "Cs") for ch in value
    ):
        raise invalid("Playlist name or track metadata is empty, too long, or contains control characters.")
    return value


def track_metadata(value):
    if not isinstance(value, dict) or set(value) - TRACK_FIELDS:
        raise invalid("Only canonical track metadata may be stored.")
    track_id = value.get("id")
    if not isinstance(track_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{11}", track_id):
        raise invalid("A canonical YouTube recording ID is required.")
    if value.get("source", "youtube") != "youtube":
        raise invalid("Only YouTube recording references are supported.")
    duration = value.get("durationMs", 0)
    if type(duration) is not int or not 0 <= duration <= 86400000:
        raise invalid("Invalid recording duration.")
    artwork = value.get("artworkUrl")
    if artwork is not None:
        artwork = text(artwork, 2048)
        try:
            url = urlsplit(artwork)
            valid = (url.scheme == "https" and url.hostname in ARTWORK_HOSTS
                     and url.username is None and url.password is None and url.port is None
                     and not any(ch.isspace() for ch in artwork))
        except ValueError:
            valid = False
        if not valid:
            raise invalid("Artwork must use an approved HTTPS thumbnail host.")
    return {"id": track_id, "source": "youtube", "title": text(value.get("title"), 300),
            "artist": text(value.get("artist"), 300), "album": text(value.get("album", ""), 300, True),
            "durationMs": duration, "artworkUrl": artwork}


def entries_metadata(value):
    if not isinstance(value, list) or len(value) > MAX_ENTRIES:
        raise invalid(f"A playlist can contain at most {MAX_ENTRIES} entries.")
    entries, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"id", "track"}:
            raise invalid("Each entry needs its own UUID and track metadata.")
        entry_id = identifier(entry["id"])
        if entry_id in seen:
            raise invalid("Entry IDs must be unique; duplicate songs need different entry IDs.")
        seen.add(entry_id)
        entries.append({"id": entry_id, "track": track_metadata(entry["track"])})
    return entries


class LibraryStore:
    def __init__(self, directory: Path):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        self.path = directory / "playlists.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        with closing(self._connect()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported playlist schema; restore a compatible backup or upgrade Ember.")
            if version == 0:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone():
                    raise RuntimeError("Unrecognized playlist database; refusing to alter it.")
                db.executescript("""
                    BEGIN IMMEDIATE;
                    CREATE TABLE IF NOT EXISTS playlists (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, revision INTEGER NOT NULL,
                        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                        deleted INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS entries (
                        playlist_id TEXT NOT NULL REFERENCES playlists(id),
                        id TEXT NOT NULL, position INTEGER NOT NULL, track TEXT NOT NULL,
                        PRIMARY KEY(playlist_id, id), UNIQUE(playlist_id, position));
                    CREATE TABLE IF NOT EXISTS receipts (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                        request_id TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                        status INTEGER NOT NULL, result TEXT NOT NULL);
                    PRAGMA user_version=1;
                    COMMIT;
                """)
            db.execute("PRAGMA journal_mode=WAL")
        os.chmod(self.path, 0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def list(self):
        with closing(self._connect()) as db:
            rows = db.execute("""SELECT p.*, COUNT(e.id) AS track_count FROM playlists p
                LEFT JOIN entries e ON e.playlist_id=p.id WHERE p.deleted=0
                GROUP BY p.id ORDER BY p.updated_at DESC, p.id""").fetchall()
        return {"playlists": [self._summary(row) for row in rows]}

    @staticmethod
    def _summary(row):
        return {"id": row["id"], "name": row["name"], "revision": row["revision"],
                "createdAt": row["created_at"], "updatedAt": row["updated_at"],
                "trackCount": row["track_count"]}

    def get(self, playlist_id):
        identifier(playlist_id)
        with closing(self._connect()) as db, db:
            # One read transaction keeps metadata and membership on the same revision.
            db.execute("BEGIN")
            row = db.execute("SELECT * FROM playlists WHERE id=? AND deleted=0", (playlist_id,)).fetchone()
            if row is None:
                raise LibraryError(404, "not_found", "Playlist not found.")
            entries = [{"id": item["id"], "track": json.loads(item["track"])} for item in
                       db.execute("SELECT id, track FROM entries WHERE playlist_id=? ORDER BY position", (playlist_id,))]
            data = self._summary({**dict(row), "track_count": len(entries)})
            data["entries"] = entries
            return data

    def mutate(self, method, playlist_id, payload):
        if not isinstance(payload, dict):
            raise invalid()
        fields = {"requestId", "expectedRevision"} if method == "DELETE" else {
            "requestId", "expectedRevision", "name", "entries"}
        if method == "POST":
            fields = {"requestId", "id", "name", "entries"}
        if set(payload) != fields:
            raise invalid("Unexpected or missing playlist fields.")
        request_id = identifier(payload["requestId"])
        playlist_id = identifier(payload["id"] if method == "POST" else playlist_id)
        if method == "POST":
            expected = 0
        else:
            expected = payload["expectedRevision"]
            if type(expected) is not int or not 1 <= expected <= 2**53 - 1:
                raise invalid("A positive expectedRevision is required.")
        name, entries = "", []
        if method != "DELETE":
            name = text(payload["name"], 120)
            entries = entries_metadata(payload["entries"])
        canonical = [method, playlist_id, expected, name, entries]
        fingerprint = hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            receipt = db.execute("SELECT * FROM receipts WHERE request_id=?", (request_id,)).fetchone()
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise LibraryError(409, "request_conflict", "Request ID already used for a different edit.")
                return receipt["status"], json.loads(receipt["result"])
            row = db.execute("SELECT revision, deleted FROM playlists WHERE id=?", (playlist_id,)).fetchone()
            if method == "POST":
                if row is not None:
                    raise LibraryError(409, "revision_conflict", "Playlist ID already exists or was deleted.")
                total, active = db.execute("SELECT COUNT(*), SUM(deleted=0) FROM playlists").fetchone()
                if total >= MAX_PLAYLIST_IDS or (active or 0) >= MAX_PLAYLISTS:
                    raise LibraryError(409, "library_full", "The playlist library has reached its limit.")
            elif row is None or row["deleted"]:
                raise LibraryError(404, "not_found", "Playlist not found.")
            elif row["revision"] != expected:
                raise LibraryError(409, "revision_conflict", "Playlist changed on another device. Reload before editing.")
            now = datetime.now(timezone.utc).isoformat()
            revision = expected + 1
            if method == "POST":
                db.execute("INSERT INTO playlists VALUES (?, ?, ?, ?, ?, 0)",
                           (playlist_id, name, revision, now, now))
            else:
                db.execute("UPDATE playlists SET name=?, revision=?, updated_at=?, deleted=? WHERE id=?",
                           (name if method != "DELETE" else "", revision, now, int(method == "DELETE"), playlist_id))
                db.execute("DELETE FROM entries WHERE playlist_id=?", (playlist_id,))
            db.executemany("INSERT INTO entries VALUES (?, ?, ?, ?)",
                           [(playlist_id, item["id"], index, json.dumps(item["track"]))
                            for index, item in enumerate(entries)])
            result = {"id": playlist_id, "revision": revision, "deleted": method == "DELETE"}
            status = 201 if method == "POST" else 200
            db.execute("INSERT INTO receipts(request_id,fingerprint,status,result) VALUES (?,?,?,?)",
                       (request_id, fingerprint, status, json.dumps(result)))
            db.execute("DELETE FROM receipts WHERE sequence NOT IN (SELECT sequence FROM receipts ORDER BY sequence DESC LIMIT ?)",
                       (MAX_RECEIPTS,))
            return status, result

    def backup(self, destination: Path):
        """Consistent SQLite backup, including committed WAL data; never overwrite."""
        destination = Path(destination)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with closing(self._connect()) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
