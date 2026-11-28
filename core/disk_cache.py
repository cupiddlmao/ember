"""Disposable, private, bounded cache; never stores login data or audio."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time

ROOT = Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'ember-player'
LIMITS = {'artwork': 200 * 1024 * 1024, 'metadata': 20 * 1024 * 1024}
LOCK = threading.RLock()
_generation = 0


def generation():
    with LOCK:
        return _generation


def _path(kind, key):
    if kind not in LIMITS:
        raise ValueError('Unknown cache kind')
    return ROOT / kind / (hashlib.sha256(key.encode()).hexdigest() + '.cache')


def get(kind, key):
    with LOCK:
        path = _path(kind, key)
        try:
            if path.stat().st_size > LIMITS[kind]:
                return None
            data = path.read_bytes()
            path.touch()
            return data
        except OSError:
            return None


def put(kind, key, data, expected_generation):
    with LOCK:
        if expected_generation != _generation or len(data) > LIMITS[kind]:
            return
        path = _path(kind, key)
        temp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, temp = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
            with os.fdopen(fd, 'wb') as out:
                out.write(data)
            os.replace(temp, path)
            entries = sorted(path.parent.glob('*.cache'), key=lambda p: p.stat().st_mtime_ns)
            total = sum(p.stat().st_size for p in entries)
            for item in entries:
                if total <= LIMITS[kind]:
                    break
                total -= item.stat().st_size
                item.unlink()
        except OSError:
            pass  # A full/unwritable cache must not prevent playback.
        finally:
            if temp:
                try:
                    Path(temp).unlink(missing_ok=True)
                except OSError:
                    pass


def get_metadata(key):
    raw = get('metadata', key)
    if raw:
        try:
            item = json.loads(raw)
            if 0 <= time.time() - item['saved_at'] < 7 * 86400:
                return item['data']
        except (ValueError, KeyError, TypeError):
            pass
    return None


def put_metadata(key, data, expected_generation):
    put('metadata', key, json.dumps({'saved_at': time.time(), 'data': data}).encode(), expected_generation)


def clear():
    global _generation
    with LOCK:
        _generation += 1
        for kind in LIMITS:
            for path in (ROOT / kind).glob('*.cache'):
                path.unlink(missing_ok=True)
