"""Validated, bounded remote artwork downloads for public catalog images."""
import urllib.error
import urllib.parse
import urllib.request


ARTWORK_HOSTS = frozenset({"i.ytimg.com", "yt3.googleusercontent.com", "lh3.googleusercontent.com"})
MAX_ARTWORK_BYTES = 2_000_000
ARTWORK_TIMEOUT = 10


def artwork_url(value):
    """Return parsed coordinates only for an explicit HTTPS image host."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        return None
    host = parsed.hostname
    if parsed.scheme != "https" or not host or host not in ARTWORK_HOSTS:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if port not in (None, 443):
        return None
    return parsed


class SafeArtworkRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse a redirect before following it to a non-artwork origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if artwork_url(newurl) is None:
            raise urllib.error.URLError("Artwork redirect is not allowed")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_artwork(url):
    """Fetch one bounded image only after validating every HTTP hop."""
    if artwork_url(url) is None:
        raise ValueError("Artwork URL is not allowed")
    request = urllib.request.Request(url, headers={"User-Agent": "EmberPlayer/0.1"})
    opener = urllib.request.build_opener(SafeArtworkRedirect())
    with opener.open(request, timeout=ARTWORK_TIMEOUT) as response:
        if artwork_url(response.geturl()) is None:
            raise ValueError("Artwork redirect is not allowed")
        data = response.read(MAX_ARTWORK_BYTES + 1)
    if len(data) > MAX_ARTWORK_BYTES:
        raise ValueError("Artwork is too large")
    return data
