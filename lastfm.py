"""Last.fm desktop authentication, now-playing updates, and scrobbling."""
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

from services import DATA, load_private, save_private

API_URL = "https://ws.audioscrobbler.com/2.0/"
AUTHORIZE_URL = "https://www.last.fm/api/auth/"
CONFIG_FILE = DATA / "lastfm.json"
FIELDS = ("api_key", "api_secret", "session_key", "username", "token")


class LastfmError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


def signature(params, secret):
    """Return Last.fm's MD5 signature; format/callback are never signed."""
    payload = "".join(
        f"{key}{params[key]}"
        for key in sorted(params)
        if key not in ("api_sig", "format", "callback")
    )
    return hashlib.md5((payload + str(secret)).encode("utf-8")).hexdigest()


def _request(params, secret):
    values = {key: str(value) for key, value in params.items() if value is not None}
    values["api_sig"] = signature(values, secret)
    values["format"] = "json"
    request = urllib.request.Request(
        API_URL,
        data=urllib.parse.urlencode(values).encode("utf-8"),
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "EmberPlayer/0.1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read(2_000_001)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read(200_000)
            payload = json.loads(raw)
        except (OSError, ValueError):
            payload = {}
        message = payload.get("message") if isinstance(payload, dict) else None
        code = payload.get("error", error.code) if isinstance(payload, dict) else error.code
        raise LastfmError(str(message or f"Last.fm returned HTTP {error.code}."), code) from None
    except (urllib.error.URLError, TimeoutError):
        raise LastfmError("Could not reach Last.fm. Check your connection and retry.") from None
    if len(raw) > 2_000_000:
        raise LastfmError("Last.fm returned an oversized response.")
    try:
        payload = json.loads(raw)
    except ValueError:
        raise LastfmError("Last.fm returned an invalid response.") from None
    if not isinstance(payload, dict):
        raise LastfmError("Last.fm returned an invalid response.")
    if payload.get("error"):
        raise LastfmError(str(payload.get("message") or "Last.fm rejected the request."), payload.get("error"))
    return payload


def load(path=None):
    path = CONFIG_FILE if path is None else path
    try:
        raw = load_private(path)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        key: str(raw[key]).strip()
        for key in FIELDS
        if raw.get(key) is not None and str(raw[key]).strip()
    }


def save(config, path=None):
    path = CONFIG_FILE if path is None else path
    payload = {
        key: str(config[key]).strip()
        for key in FIELDS
        if config.get(key) is not None and str(config[key]).strip()
    }
    save_private(path, payload)
    return payload


def connected(config):
    return isinstance(config, dict) and all(str(config.get(key) or "").strip() for key in ("api_key", "api_secret", "session_key"))


def authorize_url(api_key, token):
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode({"api_key": api_key, "token": token})


def request_token(api_key, secret):
    payload = _request({"api_key": api_key, "method": "auth.getToken"}, secret)
    token = str(payload.get("token") or "").strip()
    if not token:
        raise LastfmError("Last.fm did not return an authorization token.")
    return token


def request_session(api_key, secret, token):
    payload = _request({"api_key": api_key, "method": "auth.getSession", "token": token}, secret)
    session = payload.get("session") if isinstance(payload, dict) else None
    if not isinstance(session, dict) or not session.get("key") or not session.get("name"):
        raise LastfmError("Last.fm did not return a session. Approve Ember in the browser and try again.")
    return {"key": str(session["key"]), "name": str(session["name"])}


def _credentials(config):
    if not connected(config):
        raise LastfmError("Connect Last.fm in Settings first.")
    return str(config["api_key"]), str(config["api_secret"]), str(config["session_key"])


def _track_params(config, method, artist, track, album=None, duration=None):
    api_key, secret, session_key = _credentials(config)
    artist = str(artist or "").strip()
    title = str(track or "").strip()
    if not artist or not title:
        raise ValueError("A track needs an artist and title before it can be sent to Last.fm.")
    params = {"api_key": api_key, "method": method, "sk": session_key, "artist": artist, "track": title}
    album = str(album or "").strip()
    if album:
        params["album"] = album
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        duration = 0
    if duration > 0:
        params["duration"] = duration
    return params, secret


def now_playing(config, *, artist, track, album=None, duration=None):
    params, secret = _track_params(config, "track.updateNowPlaying", artist, track, album, duration)
    return _request(params, secret)


def scrobble(config, *, artist, track, timestamp, album=None, duration=None):
    params, secret = _track_params(config, "track.scrobble", artist, track, album, duration)
    try:
        timestamp = int(timestamp)
    except (TypeError, ValueError):
        raise ValueError("A valid playback start time is required for scrobbling.") from None
    if timestamp <= 0:
        raise ValueError("A valid playback start time is required for scrobbling.")
    params["timestamp"] = timestamp
    return _request(params, secret)


def scrobble_threshold(duration):
    """Return the listened-seconds threshold, or None for tracks that are too short."""
    try:
        duration = int(duration)
    except (TypeError, ValueError):
        return None
    if duration <= 30:
        return None
    return min(duration / 2, 240)


def should_scrobble(duration, position):
    threshold = scrobble_threshold(duration)
    try:
        return threshold is not None and float(position) >= threshold
    except (TypeError, ValueError):
        return False
