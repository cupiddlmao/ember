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
        self.max_entry_bytes = max(1, int(max_entry_bytes or self.max_bytes))
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
        try:
            files = [item for item in self.root.glob("*.m4a") if item.is_file()]
        except OSError:
            return
        total = 0
        for item in files:
            try:
                total += item.stat().st_size
            except OSError:
                continue
        if total <= self.max_bytes:
            return
        for item in sorted(files, key=lambda candidate: candidate.stat().st_mtime):
            if total <= self.max_bytes:
                break
            try:
                size = item.stat().st_size
                item.unlink()
                total -= size
            except OSError:
                continue
