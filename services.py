"""Spotify PKCE, private local settings, and bounded HTTPS requests."""
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

DATA = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "ember-player"
REDIRECT = "http://127.0.0.1:8898/callback"
SCOPES = "streaming user-read-private playlist-read-private playlist-read-collaborative user-library-read"


def save_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(data, out)
    os.replace(temp, path)


def load_private(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def request_json(url, *, headers=None, form=None):
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "EmberPlayer/0.1", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            raw = response.read(4_000_001)
            if len(raw) > 4_000_000:
                raise ValueError("Response is too large")
            return json.loads(raw)
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise RuntimeError(f"Rate limited. Try again after {error.headers.get('Retry-After', 'a few')} seconds.") from None
        messages = {401: "Authorization expired or invalid. Reconnect your account/key.", 403: "Access refused. Check application access, account eligibility and key permissions.", 404: "Nothing found for this request."}
        raise RuntimeError(messages.get(error.code, f"Service returned HTTP {error.code}. Try again later.")) from None
    except (urllib.error.URLError, TimeoutError):
        raise RuntimeError("Could not reach the service. Check your connection and retry.") from None


def valid_client_id(value):
    return bool(re.fullmatch(r"[a-fA-F0-9]{32}", value))


def callback_code(path, state):
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qs(parsed.query)
    if parsed.path != "/callback" or not hmac.compare_digest(query.get("state", [""])[0], state):
        raise ValueError("Invalid login callback")
    if "error" in query:
        raise RuntimeError("Account sign-in was declined. You can reconnect when ready.")
    code = query.get("code", [""])[0]
    if not code:
        raise ValueError("Missing authorization code")
    return code


class Spotify:
    name = "Spotify"
    authorize_url = "https://accounts.spotify.com/authorize"
    token_url = "https://accounts.spotify.com/api/token"
    api_url = "https://api.spotify.com/v1/"
    session_file = "session.json"
    scopes = SCOPES

    def credentials(self):
        return {"client_id": self.settings.get("client_id", "")}

    def check_credentials(self):
        if not valid_client_id(self.settings.get("client_id", "")):
            raise ValueError("Add your Spotify application Client ID in Settings first.")

    def __init__(self):
        self.settings = load_private(DATA / "settings.json")
        self.tokens = load_private(DATA / self.session_file)
        self.lock = threading.Lock()

    def configure(self, client_id, lyrics_key):
        if not valid_client_id(client_id):
            raise ValueError("Spotify Client ID must be 32 hexadecimal characters.")
        if client_id != self.settings.get("client_id"):
            self.tokens = {}
            (DATA / self.session_file).unlink(missing_ok=True)
        self.settings = {"client_id": client_id, "lyrics_key": lyrics_key.strip()}
        save_private(DATA / "settings.json", self.settings)

    def _store_token(self, response):
        self.tokens = {"access_token": response["access_token"], "refresh_token": response.get("refresh_token", self.tokens.get("refresh_token")), "expires_at": time.time() + response["expires_in"] - 60}
        save_private(DATA / self.session_file, self.tokens)

    def login(self):
        client_id = self.settings.get("client_id", "")
        self.check_credentials()
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        state = secrets.token_urlsafe(32)
        result = {}

        class Callback(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    code = callback_code(self.path, state)
                except ValueError:
                    self.send_error(400, "Invalid callback")
                    return
                except RuntimeError as error:
                    result["error"] = error
                else:
                    result["code"] = code
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"You can return to Ember. This window may be closed.")

            def log_message(self, *_):
                pass  # Authorization codes must never appear in logs.

        with HTTPServer(("127.0.0.1", 8898), Callback) as server:
            server.timeout = 1
            url = self.authorize_url + "?" + urllib.parse.urlencode({"client_id": client_id, "response_type": "code", "redirect_uri": REDIRECT, "scope": self.scopes, "code_challenge_method": "S256", "code_challenge": challenge, "state": state})
            if not webbrowser.open(url):
                raise RuntimeError(f"Could not open your browser for {self.name} sign-in.")
            deadline = time.monotonic() + 180
            while not result and time.monotonic() < deadline:
                server.handle_request()
        if "error" in result:
            raise result["error"]
        if "code" not in result:
            raise RuntimeError("Sign-in timed out. Click Connect to try again.")
        with self.lock:
            self._store_token(request_json(self.token_url, form={"grant_type": "authorization_code", "code": result["code"], "redirect_uri": REDIRECT, **self.credentials(), "code_verifier": verifier}))
        return self.api("me")

    def access_token(self):
        with self.lock:
            if not self.tokens.get("refresh_token"):
                raise RuntimeError(f"Connect your {self.name} account first.")
            if time.time() >= self.tokens.get("expires_at", 0):
                self._store_token(request_json(self.token_url, form={"grant_type": "refresh_token", "refresh_token": self.tokens["refresh_token"], **self.credentials()}))
            return self.tokens["access_token"]

    def api(self, path, params=None):
        url = self.api_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        return request_json(url, headers={"Authorization": "Bearer " + self.access_token()})

    def logout(self):
        with self.lock:
            self.tokens = {}
            (DATA / self.session_file).unlink(missing_ok=True)


class SoundCloudAccount(Spotify):
    name = "SoundCloud"
    authorize_url = "https://secure.soundcloud.com/authorize"
    token_url = "https://secure.soundcloud.com/oauth/token"
    api_url = "https://api.soundcloud.com/"
    session_file = "soundcloud-session.json"
    scopes = ""

    def __init__(self):
        self.settings = load_private(DATA / "soundcloud-settings.json")
        self.tokens = load_private(DATA / self.session_file)
        self.lock = threading.Lock()

    def credentials(self):
        return {key: self.settings.get(key, "") for key in ("client_id", "client_secret")}

    def check_credentials(self):
        if not all(self.credentials().values()):
            raise ValueError("Add your SoundCloud application Client ID and secret in Settings first.")

    def configure(self, client_id, client_secret):
        settings = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
        if settings != self.settings:
            self.logout()
        save_private(DATA / "soundcloud-settings.json", settings)
        self.settings = settings

    def api(self, path, params=None):
        # Pagination URLs must never send the account token to another host.
        url = urllib.parse.urljoin(self.api_url, path)
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.soundcloud.com" or parsed.fragment:
            raise ValueError("Invalid SoundCloud API URL")
        if params:
            url += ("&" if parsed.query else "?") + urllib.parse.urlencode(params)
        return request_json(url, headers={"Authorization": "OAuth " + self.access_token()})

    def playlists(self):
        items = []
        path = "me/playlists?linked_partitioning=true&limit=50"
        while path:
            data = self.api(path)
            items.extend(data if isinstance(data, list) else data.get("collection", []))
            path = data.get("next_href") if isinstance(data, dict) else None
            if len(items) >= 1000:
                break  # shortcut: cap account playlists at 1000; add UI paging if needed.
        return items
