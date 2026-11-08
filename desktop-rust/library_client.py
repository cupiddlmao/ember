"""Private single-library client; never returns or logs the access key."""
import copy
import fcntl
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024


def root():
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "ember-player/rust-library"


def private_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > 24 * 1024 * 1024:
        raise ValueError("Playlist cache exceeds its storage limit.")
    fd, temp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temp).unlink(missing_ok=True)


def read_json(path, limit=24 * 1024 * 1024):
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Private library file is too large.")
    return json.loads(data)


def connection():
    config = read_json(root() / "connection.json", 16384)
    address = str(config.get("server", "")).strip().rstrip("/")
    key = str(config.get("key", "")).strip()
    parsed = urllib.parse.urlsplit(address)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(ch.isspace() or ord(ch) < 32 for ch in address)
            or len(address) > 2048
            or not (parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}))):
        raise ValueError("Use an HTTPS server address, including /ember if configured. HTTP is only allowed on loopback.")
    if not key or len(key) > 4096 or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
        raise ValueError("Enter the existing Ember access key in Settings.")
    return address, key


class Rejected(Exception):
    def __init__(self, status):
        self.status = status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request(server, key, method, suffix="", body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if data is not None and len(data) > 8 * 1024 * 1024:
        raise Rejected(413)
    headers = {"Authorization": "Bearer " + key, "Accept": "application/json"}
    if data is not None:
        headers.update({"Content-Type": "application/json", "Content-Length": str(len(data))})
    req = urllib.request.Request(server + "/v1/library/playlists" + suffix, data=data, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=15) as response:
            raw = response.read(MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise Rejected(error.code) from None
    if len(raw) > MAX_BYTES:
        raise ValueError("Playlist response is too large.")
    return json.loads(raw)


def identifier(value):
    if not isinstance(value, str) or str(uuid.UUID(value)) != value:
        raise ValueError("Invalid playlist identifier.")
    return value


def name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 120 or any(ord(c) < 32 for c in value):
        raise ValueError("Playlist names must contain 1–120 characters.")
    return value.strip()


def empty_state():
    return {"playlists": [], "detail": None, "pending": None}


def execute(command):
    try:
        server, key = connection()
    except FileNotFoundError:
        return {**empty_state(), "pending": False, "online": False, "error": "Configure the shared playlist server and access key in Ember Settings."}
    except (OSError, ValueError, TypeError):
        return {**empty_state(), "pending": False, "online": False, "error": "The private playlist connection file cannot be read. Restore or remove it in Settings, then retry."}
    path = root() / (hashlib.sha256(server.encode()).hexdigest() + ".json")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root() / "library.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            state = read_json(path) if path.exists() else empty_state()
            if not isinstance(state, dict) or not isinstance(state.get("playlists"), list):
                raise ValueError("Invalid cache")
        except (OSError, ValueError, TypeError):
            # Do not overwrite an unreadable pending mutation.
            return {**empty_state(), "pending": False, "online": False, "error": "Private library cache cannot be read. The file was left untouched; restore it before editing."}
        state["online"] = False
        state["error"] = ""
        action = command.get("operation", "refresh")
        acknowledged = False
        try:
            if action == "retry":
                if state.get("pending") is None:
                    raise ValueError("There is no pending save.")
            elif action == "discard":
                if state.get("pending") is None:
                    raise ValueError("There is no pending save to discard.")
                pending = state["pending"]
                state["pending"] = None
                try:
                    private_save(path, state)
                except OSError:
                    state["pending"] = pending
                    raise
            elif action in {"create", "rename", "delete", "add", "remove", "move"}:
                if state.get("pending") is not None:
                    raise ValueError("Retry the pending save before editing.")
                # A fresh read is required before preparing any write.
                state["playlists"] = request(server, key, "GET")["playlists"]
                if action == "create":
                    pid = str(uuid.uuid4())
                    body = {"id": pid, "requestId": str(uuid.uuid4()), "name": name(command.get("name")), "entries": []}
                    method, suffix, revision = "POST", "", 1
                else:
                    pid = identifier(command.get("id"))
                    detail = request(server, key, "GET", "/" + pid)
                    if action != "add" and detail["revision"] != command.get("revision"):
                        state["detail"] = detail
                        raise Rejected(409)
                    body = {"requestId": str(uuid.uuid4()), "expectedRevision": detail["revision"]}
                    method, suffix, revision = "PUT", "/" + pid, detail["revision"] + 1
                    if action == "delete":
                        method = "DELETE"
                    else:
                        entries = copy.deepcopy(detail["entries"])
                        title = detail["name"]
                        if action == "rename":
                            title = name(command.get("name"))
                        elif action == "add":
                            if len(entries) >= 5000:
                                raise ValueError("This playlist already has 5000 songs.")
                            entries.append({"id": str(uuid.uuid4()), "track": command["track"]})
                        else:
                            eid = identifier(command.get("entry"))
                            index = next((i for i, entry in enumerate(entries) if entry["id"] == eid), None)
                            if index is None:
                                raise Rejected(409)
                            if action == "remove":
                                entries.pop(index)
                            else:
                                delta = command.get("delta")
                                if delta not in (-1, 1) or not 0 <= index + delta < len(entries):
                                    raise ValueError("Cannot move this song further.")
                                entries[index], entries[index + delta] = entries[index + delta], entries[index]
                        body.update({"name": title, "entries": entries})
                state["pending"] = {"method": method, "suffix": suffix, "body": body, "id": pid, "revision": revision}
                try:
                    private_save(path, state)
                except Exception:
                    state["pending"] = None
                    raise
            elif action not in {"refresh", "load"}:
                raise ValueError("Unknown library operation.")

            if action == "retry" or action in {"create", "rename", "delete", "add", "remove", "move"}:
                pending = state["pending"]
                receipt = request(server, key, pending["method"], pending["suffix"], pending["body"])
                if (receipt.get("id") != pending["id"] or receipt.get("revision") != pending["revision"]
                        or receipt.get("deleted") is not (pending["method"] == "DELETE")):
                    raise ValueError("Save receipt could not be verified. Retry the pending save.")
                state["pending"] = None
                try:
                    private_save(path, state)
                except OSError:
                    state["pending"] = pending
                    raise
                acknowledged = True
                if pending["method"] == "DELETE":
                    state["detail"] = None
                else:
                    state["detail"] = request(server, key, "GET", "/" + pending["id"])
            elif action == "load":
                pid = identifier(command.get("id"))
                if not state.get("detail") or state["detail"].get("id") != pid:
                    state["detail"] = None
                state["detail"] = request(server, key, "GET", "/" + pid)
            state["playlists"] = request(server, key, "GET")["playlists"]
            ids = {p["id"] for p in state["playlists"]}
            if state.get("detail") and state["detail"]["id"] not in ids:
                state["detail"] = None
            if action == "refresh" and state.get("detail"):
                state["detail"] = request(server, key, "GET", "/" + state["detail"]["id"])
            state["online"] = True
            private_save(path, state)
        except Rejected as error:
            # A server rejection never proves the mutation was safe to forget.
            # Keep it until the user explicitly discards it.
            try:
                private_save(path, state)
            except OSError:
                pass
            messages = {
                400: "The server rejected this save. Discard the pending save, refresh, and try again.",
                401: "Access key rejected. Fix the key in Settings, then retry the pending save.",
                404: "Playlist missing, or update the laptop backend to 0.3.0. Discard the pending save after refreshing.",
                409: "Playlist changed on another device. Refresh, then use Discard pending save to reapply your edit.",
                413: "This save is too large for the server. Discard the pending save before trying again.",
                415: "The server rejected this save format. Discard the pending save before trying again.",
            }
            state["error"] = messages.get(error.status, "Library request rejected. Retry or discard the pending save.")
        except Exception:
            if state.get("pending"):
                state["error"] = "Save not confirmed. Retry the pending save before editing."
            elif acknowledged:
                state["error"] = "Saved, but refresh failed. Refresh to see the latest library."
            else:
                state["error"] = "Library unavailable. Cached playlists are read only; check Settings and Tailscale."
        state["acknowledged"] = acknowledged
        # Return pending existence, never expose an entire retry command to the UI.
        return {**state, "pending": state.get("pending") is not None}
