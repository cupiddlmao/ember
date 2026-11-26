"""Bounded on-disk cache for converted M4A audio.

Only server-generated M4A files live here. Upstream URLs, cookies, access keys
and track metadata are deliberately not stored.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import threading
import uuid
from pathlib import Path
from typing import Callable


class AudioCache:
    def __init__(self, root: Path, max_bytes: int, max_entry_bytes: int | None = None):
        self.root = Path(root)
        self.max_bytes = max(1, int(max_bytes))
        # A per-entry cap above the whole-cache budget would let a fresh entry
        # evict itself, leaving get_or_create to return a path to a file it had
        # just deleted.
        self.max_entry_bytes = min(max(1, int(max_entry_bytes or self.max_bytes)), self.max_bytes)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._evict()

    def path_for(self, key: str) -> Path:
        return self.root / f"{key}.m4a"

    def _key_lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def get_or_create(self, key: str, create: Callable[[Path], Path]) -> Path:
        """Return a cached file, creating it once even under concurrent calls.

        ``create`` receives a private work directory and must return a complete
        audio file inside it. The work directory is always removed.
        """
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.path_for(key)
        with self._key_lock(key):
            try:
                if path.is_file() and path.stat().st_size > 0:
                    os.utime(path, None)
                    return path
            except OSError:
                pass
            path.unlink(missing_ok=True)
            work = Path(tempfile.mkdtemp(prefix=".work-", dir=self.root))
            temporary = self.root / f".{key}-{uuid.uuid4().hex}.m4a"
            try:
                generated = Path(create(work))
                if not generated.is_file() or generated.stat().st_size <= 0:
                    raise RuntimeError("Audio conversion produced no file.")
                if generated.stat().st_size > self.max_entry_bytes:
                    raise RuntimeError("Audio file is larger than the allowed limit.")
                if temporary.exists():
                    temporary.unlink()
                os.replace(generated, temporary)
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
                os.utime(path, None)
            finally:
                shutil.rmtree(work, ignore_errors=True)
                temporary.unlink(missing_ok=True)
            self._evict()
            return path

    def _evict(self) -> None:
        """Drop least-recently-used entries until the cache fits ``max_bytes``.

        ``get_or_create`` touches an entry on every hit, so mtime is how this
        cache records use. Ties are broken by name because coarse timestamps,
        or a cache restored from a backup, can give every entry the same mtime,
        and eviction must not then depend on the order the filesystem lists.
        """
        try:
            found = [item for item in self.root.glob("*.m4a") if item.is_file()]
        except OSError:
            return
        entries, total = [], 0
        for item in found:
            try:
                stats = item.stat()
            except OSError:
                # Gone since the listing, so it no longer occupies the budget.
                continue
            entries.append((stats.st_mtime_ns, item.name, item, stats.st_size))
            total += stats.st_size
        if total <= self.max_bytes:
            return
        for _modified, _name, item, size in sorted(entries, key=lambda entry: entry[:2]):
            if total <= self.max_bytes:
                break
            try:
                item.unlink()
            except OSError:
                # Unreadable or already removed: nothing was reclaimed.
                continue
            total -= size
