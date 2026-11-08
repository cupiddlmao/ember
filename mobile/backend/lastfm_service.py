"""One-account Last.fm authorization and a durable, bounded scrobble outbox."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .library_store import LibraryError


class LastFM:
    def __init__(self, settings, call=None):
        self.key, self.secret = settings.lastfm_api_key, settings.lastfm_api_secret
        root = settings.library_dir
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = root / "lastfm.sqlite3"
        self.lock = threading.RLock()
        self.call = call or self._call
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS account (id INTEGER PRIMARY KEY, session TEXT, name TEXT, token TEXT, created REAL)")
            db.execute("INSERT OR IGNORE INTO account(id) VALUES(1)")
            db.execute("CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY, body TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending')")
        os.chmod(self.path, 0o600)
        self.wake = threading.Event()
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self._work, daemon=True, name="ember-lastfm")
        self.worker.start()

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def close(self):
        self.closed.set()
        self.wake.set()
        self.worker.join(timeout=12)

    def _call(self, method, **params):
        params.update(method=method, api_key=self.key)
        signature = "".join(k + str(params[k]) for k in sorted(params)) + self.secret
        params["api_sig"] = hashlib.md5(signature.encode()).hexdigest()
        params["format"] = "json"
        request = Request("https://ws.audioscrobbler.com/2.0/", data=urlencode(params).encode(), method="POST")
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError()
            data = json.loads(raw)
            if "error" in data:
                # Do not expose the upstream response, signatures or session keys.
                if data["error"] == 9:
                    with self.db() as db:
                        db.execute("UPDATE account SET session=NULL WHERE id=1")
                raise ValueError()
            return data
        except Exception:
            raise LibraryError(502, "lastfm_unavailable", "Last.fm could not complete this request. Retry shortly.") from None

    def recommendations(self, body):
        title, artist = body.get("title"), body.get("artist")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 300 or any(ord(c) < 32 for c in v) for v in (title, artist)):
            raise LibraryError(400, "invalid_track", "A track title and artist are required.")
        if not self.key:
            raise LibraryError(503, "lastfm_not_configured", "Configure Last.fm API credentials on the Ember server for Song radio.")
        # Public recommendations do not need the listener's session or the scrobble lock.
        data = self.call("track.getSimilar", track=title.strip(), artist=artist.strip(), autocorrect=1, limit=12)
        similar = data.get("similartracks") if isinstance(data, dict) else None
        if not isinstance(similar, dict):
            raise LibraryError(502, "lastfm_unavailable", "Last.fm returned invalid recommendations.")
        rows = similar.get("track") or []
        if not isinstance(rows, list):
            raise LibraryError(502, "lastfm_unavailable", "Last.fm returned invalid recommendations.")
        if not rows:
            data = self.call("artist.getSimilar", artist=artist.strip(), autocorrect=1, limit=3)
            similar = data.get("similarartists") if isinstance(data, dict) else None
            artists = similar.get("artist") if isinstance(similar, dict) else None
            if not isinstance(artists, list):
                raise LibraryError(502, "lastfm_unavailable", "Last.fm returned invalid similar artists.")
            names = []
            for item in artists[:3]:
                name = item.get("name") if isinstance(item, dict) else None
                if (isinstance(name, str) and name.strip() and len(name) <= 300
                        and not any(ord(c) < 32 for c in name)
                        and name.casefold() not in {n.casefold() for n in names}):
                    names.append(name)

            def top_tracks(name):
                try:
                    data = self.call("artist.getTopTracks", artist=name, autocorrect=1, limit=4)
                except LibraryError:
                    return []
                top = data.get("toptracks") if isinstance(data, dict) else None
                tracks = top.get("track") if isinstance(top, dict) else None
                return tracks[:4] if isinstance(tracks, list) else []

            # Fetch at most three artists concurrently within the clients' radio timeout.
            with ThreadPoolExecutor(max_workers=3) as pool:
                rows = [row for tracks in pool.map(top_tracks, names) for row in tracks]
        result = []
        seen = {(title.strip().casefold(), artist.strip().casefold())}
        for row in rows[:12]:
            if not isinstance(row, dict):
                continue
            name, artist = row.get("name"), row.get("artist")
            artist = artist.get("name") if isinstance(artist, dict) else None
            if any(not isinstance(v, str) or not v.strip() or len(v) > 300 or any(ord(c) < 32 for c in v) for v in (name, artist)):
                continue
            identity = (name.casefold(), artist.casefold())
            if identity not in seen:
                result.append({"title": name, "artist": artist})
                seen.add(identity)
        return result

    def handle(self, body):
        if not isinstance(body, dict):
            raise LibraryError(400, "invalid_body", "Expected a JSON object.")
        action = body.get("action")
        with self.lock:
            with self.db() as db:
                session, name, token, created = db.execute("SELECT session,name,token,created FROM account WHERE id=1").fetchone()
                pending = db.execute("SELECT count(*) FROM outbox WHERE state='pending'").fetchone()[0]
            if action == "status":
                return {"configured": bool(self.key and self.secret), "connected": bool(session), "username": name or "", "pending": pending}
            if action == "disconnect":
                with self.db() as db:
                    db.execute("UPDATE account SET session=NULL,name=NULL,token=NULL,created=NULL WHERE id=1")
                    db.execute("DELETE FROM outbox")
                return {"ok": True}
            if not self.key or not self.secret:
                raise LibraryError(503, "lastfm_not_configured", "Set LASTFM_API_KEY and LASTFM_API_SECRET on the Ember server first.")
            if action == "connect":
                token = self.call("auth.getToken")["token"]
                with self.db() as db:
                    db.execute("UPDATE account SET token=?,created=? WHERE id=1", (token, time.time()))
                return {"url": "https://www.last.fm/api/auth/?" + urlencode({"api_key": self.key, "token": token})}
            if action == "finish":
                if not token or not created or time.time() - created > 3600:
                    raise LibraryError(400, "authorization_expired", "Start Connect again, approve in the browser, then finish.")
                account = self.call("auth.getSession", token=token)["session"]
                # A different account must not inherit old listening history.
                with self.db() as db:
                    if name and name != account["name"]:
                        db.execute("DELETE FROM outbox")
                    db.execute("UPDATE account SET session=?,name=?,token=NULL,created=NULL WHERE id=1", (account["key"], account["name"]))
                self.wake.set()
                return {"ok": True, "username": account["name"]}
            if not session:
                raise LibraryError(409, "lastfm_disconnected", "Connect Last.fm in Settings first.")
            if action not in ("now", "scrobble"):
                raise LibraryError(400, "invalid_action", "Unknown Last.fm action.")
            title, artist = body.get("title"), body.get("artist")
            if any(not isinstance(v, str) or not v.strip() or len(v) > 500 or any(ord(c) < 32 for c in v) for v in (title, artist)):
                raise LibraryError(400, "invalid_track", "A track title and artist are required.")
            album = body.get("album", "")
            if not isinstance(album, str) or len(album) > 500 or any(ord(c) < 32 for c in album):
                raise LibraryError(400, "invalid_album", "Album metadata must be text of at most 500 characters.")
            metadata = {"track": title, "artist": artist}
            if album.strip():
                metadata["album"] = album.strip()
            if action == "now":
                self.call("track.updateNowPlaying", sk=session, **metadata)
                return {"ok": True}
            duration, listened, started = (body.get(k) for k in ("duration", "listened", "started"))
            if any(type(v) is not int for v in (duration, listened, started)) or not 30 < duration <= 86400 or not min(duration / 2, 240) <= listened <= 86400 or not time.time() - 14 * 86400 <= started <= time.time() + 60:
                raise LibraryError(400, "invalid_listen", "The track has not met Last.fm listening requirements, or its timestamp is invalid.")
            try:
                event_id = str(uuid.UUID(body.get("id", "")))
            except (ValueError, TypeError, AttributeError):
                raise LibraryError(400, "invalid_id", "A playback UUID is required.") from None
            payload = json.dumps({**metadata, "timestamp": started, "duration": duration}, sort_keys=True)
            with self.db() as db:
                old = db.execute("SELECT body FROM outbox WHERE id=?", (event_id,)).fetchone()
                if old and old[0] != payload:
                    raise LibraryError(409, "event_conflict", "This playback ID already has different metadata.")
                if not old:
                    # Keep receipts for 14 days so app retries cannot enqueue duplicates.
                    db.execute("DELETE FROM outbox WHERE json_extract(body,'$.timestamp') < ?", (time.time() - 14 * 86400,))
                    if db.execute("SELECT count(*) FROM outbox").fetchone()[0] >= 20000:
                        raise LibraryError(503, "outbox_full", "Last.fm queue is full. Retry later.")
                    db.execute("INSERT INTO outbox(id,body) VALUES(?,?)", (event_id, payload))
            self.wake.set()
            return {"ok": True}

    def flush(self):
        with self.lock, self.db() as db:
            session = db.execute("SELECT session FROM account WHERE id=1").fetchone()[0]
            if not session:
                return
            for event_id, raw in db.execute("SELECT id,body FROM outbox WHERE state='pending' ORDER BY rowid LIMIT 1").fetchall():
                payload = json.loads(raw)
                if payload["timestamp"] < time.time() - 14 * 86400:
                    db.execute("DELETE FROM outbox WHERE id=?", (event_id,))
                    continue
                result = self.call("track.scrobble", sk=session, **payload)
                scrobbles = result.get("scrobbles", {})
                accepted = int(scrobbles.get("@attr", {}).get("accepted", 0)) == 1
                item = scrobbles.get("scrobble", {})
                if isinstance(item, list):
                    item = item[0] if item else {}
                ignored = str(item.get("ignoredMessage", {}).get("code", ""))
                if not accepted and ignored not in ("1", "2", "3"):
                    raise LibraryError(502, "lastfm_rejected", "Last.fm did not accept the scrobble.")
                db.execute("UPDATE outbox SET state=? WHERE id=?", ("sent" if accepted else "rejected", event_id))
                db.commit()
                if db.execute("SELECT 1 FROM outbox WHERE state='pending' LIMIT 1").fetchone():
                    self.wake.set()

    def _work(self):
        while not self.closed.is_set():
            self.wake.wait(60)
            self.wake.clear()
            if self.closed.is_set():
                return
            try:
                self.flush()
            except Exception:
                pass  # Retain the durable outbox and retry on the next wake.
