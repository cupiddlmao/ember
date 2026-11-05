"""Private, file-backed audio downloads and a bounded automatic cache.

The existing disk_cache module is intentionally byte-oriented and capped at a
few hundred MiB. Audio belongs in files: it can be several MiB per track,
GStreamer can play it directly, and eviction should never load it into RAM.
"""
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import music

DEFAULT_LIMIT = 2 * 1024 ** 3
_CACHE_HOME = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache'))
_DATA_HOME = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share'))
DEFAULT_CACHE_ROOT = _CACHE_HOME / 'ember-player' / 'audio'
DEFAULT_DOWNLOAD_ROOT = _DATA_HOME / 'ember-player' / 'downloads'
DEFAULT_INDEX_PATH = _DATA_HOME / 'ember-player' / 'audio-index.sqlite3'
_VIDEO = re.compile(r'[\w-]{11}', re.ASCII)
_SUFFIX = re.compile(r'\.[A-Za-z0-9]{1,8}', re.ASCII)


class AudioStore:
    """Store audio as owner-only files, keyed by public YouTube video ID."""

    def __init__(self, cache_root=None, download_root=None, index_path=None, limit=None):
        self.cache_root = Path(cache_root or DEFAULT_CACHE_ROOT)
        self.download_root = Path(download_root or DEFAULT_DOWNLOAD_ROOT)
        self.index_path = Path(index_path or DEFAULT_INDEX_PATH)
        self.limit = max(0, int(limit if limit is not None else DEFAULT_LIMIT))
        self.lock = threading.RLock()

    @contextmanager
    def _db(self):
        self.index_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        con = sqlite3.connect(str(self.index_path), timeout=15)
        con.row_factory = sqlite3.Row
        try:
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('PRAGMA synchronous=NORMAL')
            con.execute('''
                CREATE TABLE IF NOT EXISTS audio (
                    video_id TEXT PRIMARY KEY,
                    path TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    pinned INTEGER NOT NULL DEFAULT 0,
                    track_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    last_used_at REAL NOT NULL
                )
            ''')
            try:
                os.chmod(self.index_path, 0o600)
            except OSError:
                pass
            with con:
                yield con
        finally:
            con.close()

    def _ensure_root(self, root):
        root.mkdir(parents=True, exist_ok=True, mode=0o700)

    @staticmethod
    def _video(track):
        video = str((track or {}).get('id') or '')
        if not _VIDEO.fullmatch(video):
            raise ValueError('Invalid YouTube track ID.')
        return video

    @staticmethod
    def _suffix(value):
        value = str(value or '')
        return value.lower() if _SUFFIX.fullmatch(value) else '.audio'

    def _file_for(self, root, video, suffix):
        digest = hashlib.sha256(video.encode()).hexdigest()
        return root / digest[:2] / (digest + self._suffix(suffix))

    def _safe_file(self, path):
        try:
            resolved = Path(path).resolve()
            return any(resolved.is_relative_to(root.resolve())
                       for root in (self.cache_root, self.download_root))
        except (OSError, ValueError):
            return False

    @staticmethod
    def _metadata(track, resolved):
        data = dict(resolved if isinstance(resolved, dict) else track)
        for key, value in (track or {}).items():
            if key not in data or data[key] in (None, '', [], {}):
                data[key] = value
        return data

    def _delete_row(self, video):
        with self._db() as con:
            con.execute('DELETE FROM audio WHERE video_id=?', (video,))

    def _item(self, row):
        if row is None:
            return None
        path = Path(row['path'])
        if not self._safe_file(path):
            return None
        try:
            size = path.stat().st_size
        except OSError:
            return None
        if size <= 0:
            return None
        try:
            track = json.loads(row['track_json'])
        except (TypeError, ValueError):
            track = None
        if not isinstance(track, dict):
            track = None
        return {
            'video_id': row['video_id'],
            'path': path,
            'bytes': size,
            'pinned': bool(row['pinned']),
            'track': track,
            'created_at': row['created_at'],
            'last_used_at': row['last_used_at'],
        }

    def entry(self, video):
        """Return a ready entry without changing its recency."""
        video = str(video or '')
        if not _VIDEO.fullmatch(video):
            return None
        with self._db() as con:
            row = con.execute('SELECT * FROM audio WHERE video_id=?', (video,)).fetchone()
        item = self._item(row)
        if row is not None and item is None:
            self._delete_row(video)
        return item

    def entries(self, videos=None):
        """Return ready entries for many video IDs using one database query."""
        wanted = {str(value) for value in videos} if videos is not None else None
        with self._db() as con:
            rows = con.execute('SELECT * FROM audio').fetchall()
        result, stale = {}, []
        for row in rows:
            if wanted is not None and row['video_id'] not in wanted:
                continue
            item = self._item(row)
            if item is None:
                stale.append(row['video_id'])
            else:
                result[row['video_id']] = item
        for video in stale:
            self._delete_row(video)
        return result

    def lookup(self, track):
        """Return a playable local entry, touching its LRU timestamp."""
        video = self._video(track)
        item = self.entry(video)
        if item is None:
            return None
        now = time.time()
        with self.lock, self._db() as con:
            con.execute('UPDATE audio SET last_used_at=? WHERE video_id=?', (now, video))
        item['last_used_at'] = now
        return item

    def _move(self, source, destination):
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.replace(source, destination)
        except OSError:
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
            source.unlink(missing_ok=True)
        try:
            os.chmod(destination, 0o600)
        except OSError:
            pass

    def download(self, track, pinned=False, protect=None):
        """Download a track, or return/promote an existing copy.

        ``protect`` is a video ID that automatic LRU eviction must not remove
        (normally the currently playing track).
        """
        video = self._video(track)
        existing = self.entry(video)
        if existing:
            if pinned and not existing['pinned']:
                return self.pin(video)
            return existing

        root = self.download_root if pinned else self.cache_root
        self._ensure_root(root)
        temp_parent = root / 'tmp'
        temp_parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        work = Path(tempfile.mkdtemp(prefix='audio-', dir=temp_parent))
        try:
            resolved, source = music.download_audio(track, work)
            source = Path(source)
            if not source.is_file():
                raise RuntimeError('YouTube did not produce an audio file.')
            destination = self._file_for(root, video, source.suffix)
            self._move(source, destination)
            data = self._metadata(track, resolved)
            now = time.time()
            with self.lock, self._db() as con:
                con.execute('''
                    INSERT INTO audio
                        (video_id, path, bytes, pinned, track_json, created_at, last_used_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(video_id) DO UPDATE SET
                        path=excluded.path,
                        bytes=excluded.bytes,
                        pinned=excluded.pinned,
                        track_json=excluded.track_json,
                        last_used_at=excluded.last_used_at
                ''', (video, str(destination), destination.stat().st_size, int(bool(pinned)),
                      json.dumps(data, ensure_ascii=False), now, now))
            item = self.entry(video)
            if item is None:
                raise RuntimeError('The downloaded audio file could not be indexed.')
            if not pinned:
                self.enforce_limit(self.limit, protect=protect)
                item = self.entry(video)
            return item
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def pin(self, video):
        """Move an automatic-cache copy into the permanent downloads store."""
        item = self.entry(video)
        if item is None:
            raise RuntimeError('That downloaded track is no longer available.')
        if item['pinned']:
            return item
        destination = self._file_for(self.download_root, video, item['path'].suffix)
        self._ensure_root(self.download_root)
        self._move(item['path'], destination)
        now = time.time()
        with self.lock, self._db() as con:
            con.execute('UPDATE audio SET path=?, pinned=1, last_used_at=? WHERE video_id=?',
                        (str(destination), now, video))
        updated = self.entry(video)
        if updated is None:
            raise RuntimeError('Could not promote the downloaded audio file.')
        return updated

    def remove(self, video):
        """Remove one downloaded or cached file and its index row."""
        item = self.entry(video)
        if item is None:
            return False
        if self._safe_file(item['path']):
            item['path'].unlink(missing_ok=True)
        self._delete_row(video)
        return True

    def clear_auto(self):
        """Delete automatic cache files, preserving pinned offline downloads."""
        with self.lock, self._db() as con:
            rows = con.execute('SELECT video_id, path FROM audio WHERE pinned=0').fetchall()
            for row in rows:
                path = Path(row['path'])
                if self._safe_file(path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
            con.execute('DELETE FROM audio WHERE pinned=0')

    def _clear_temps(self):
        for root in (self.cache_root, self.download_root):
            temp = root / 'tmp'
            if temp.is_dir():
                for child in temp.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child, ignore_errors=True)
                    else:
                        child.unlink(missing_ok=True)

    def enforce_limit(self, limit=None, protect=None):
        """Evict the least-recently-used automatic entries above the cap."""
        limit = self.limit if limit is None else max(0, int(limit))
        with self.lock, self._db() as con:
            rows = con.execute('''
                SELECT video_id, path, bytes FROM audio
                WHERE pinned=0 ORDER BY last_used_at ASC, created_at ASC
            ''').fetchall()
            total = sum(int(row['bytes']) for row in rows)
            for row in rows:
                if total <= limit:
                    break
                if protect and row['video_id'] == protect:
                    continue
                path = Path(row['path'])
                if self._safe_file(path):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        continue
                total -= int(row['bytes'])
                con.execute('DELETE FROM audio WHERE video_id=?', (row['video_id'],))

    def stats(self):
        with self._db() as con:
            rows = con.execute('SELECT pinned, bytes FROM audio').fetchall()
        auto = sum(int(row['bytes']) for row in rows if not row['pinned'])
        pinned = sum(int(row['bytes']) for row in rows if row['pinned'])
        return {'auto_bytes': auto, 'pinned_bytes': pinned, 'auto_count': sum(1 for row in rows if not row['pinned']),
                'pinned_count': sum(1 for row in rows if row['pinned'])}

    def downloaded_tracks(self):
        """Return pinned tracks as normalized metadata for the Downloads page."""
        with self._db() as con:
            rows = con.execute('SELECT * FROM audio WHERE pinned=1 ORDER BY created_at DESC').fetchall()
        tracks = []
        for row in rows:
            item = self._item(row)
            if item is None:
                self._delete_row(row['video_id'])
                continue
            if item['track']:
                tracks.append(item['track'])
        return tracks

    def cleanup(self):
        """Drop stale index rows and abandoned temporary download directories."""
        with self._db() as con:
            rows = con.execute('SELECT * FROM audio').fetchall()
        for row in rows:
            if self._item(row) is None:
                self._delete_row(row['video_id'])
        self._clear_temps()

    def set_limit(self, limit):
        self.limit = max(0, int(limit))
        self.enforce_limit(self.limit)


STORE = AudioStore()
