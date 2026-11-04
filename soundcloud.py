"""Public SoundCloud tracks, resolved without loading the advertising web player."""
import urllib.parse
import yt_dlp


class Quiet:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


def soundcloud_url(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname not in ("soundcloud.com", "www.soundcloud.com") or parsed.username or parsed.password:
        raise ValueError("Use a public https://soundcloud.com/artist/track or playlist link.")
    return urllib.parse.urlunsplit(("https", "soundcloud.com", parsed.path, "", ""))


def normalize(info):
    title = info.get("track") or info.get("title") or "Untitled"
    artist = info.get("artist") or info.get("uploader") or "Unknown artist"
    url = info.get("webpage_url") or info.get("original_url") or info.get("url", "")
    images = [{"url": info["thumbnail"]}] if info.get("thumbnail") else [{"url": item["url"]} for item in info.get("thumbnails", []) if item.get("url")]
    return {"source": "soundcloud", "type": "track", "id": str(info["id"]), "uri": soundcloud_url(url), "name": title, "artists": [{"name": artist}], "album": {"images": images}, "duration_ms": round((info.get("duration") or 0) * 1000)}


def extract(query, flat=False, metadata=False):
    # shortcut: public playlists load their first 30 tracks; add paging for larger libraries.
    options = {"logger": Quiet(), "quiet": True, "no_warnings": True, "extract_flat": "in_playlist" if flat and not metadata else False, "ignore_no_formats_error": metadata, "ignoreerrors": metadata, "playlistend": 30, "socket_timeout": 15, "retries": 1, "extractor_retries": 1, "skip_download": True}
    try:
        with yt_dlp.YoutubeDL(options) as client:
            return client.extract_info(query, download=False)
    except yt_dlp.utils.DownloadError as error:
        message = str(error).lower()
        if "drm" in message:
            raise RuntimeError("This SoundCloud track is DRM-protected and cannot play in Ember. Try another freely streamable original.") from None
        raise RuntimeError("SoundCloud could not provide this track. It may be private, unavailable, or require a subscription.") from None


def search(query):
    if query.startswith(("https://", "http://")):
        url = soundcloud_url(query)
        info = extract(url, flat=True, metadata="/sets/" in urllib.parse.urlsplit(url).path)
    else:
        info = extract("scsearch20:" + query, flat=True)
    entries = info.get("entries") if "entries" in info else [info]
    result = []
    for entry in entries or []:
        if entry:
            # Resolve the public URL from flat metadata using its explicit permalink.
            entry = dict(entry)
            if not entry.get("webpage_url") and entry.get("permalink_url"):
                entry["webpage_url"] = entry["permalink_url"]
            result.append(normalize(entry))
    return result


def resolve(track):
    info = extract(soundcloud_url(track["uri"]))
    formats = [item for item in info.get("formats", []) if item.get("url") and "preview" not in item.get("format_id", "") and not item.get("has_drm") and item.get("protocol") in ("http", "https", "m3u8_native")]
    if not formats:
        raise RuntimeError("No full, unrestricted stream is available for this SoundCloud track. Previews and paid-only tracks are not substituted.")
    stream = formats[-1]["url"]
    parsed = urllib.parse.urlsplit(stream)
    if parsed.scheme != "https" or not ((parsed.hostname or "").endswith(".sndcdn.com") or parsed.hostname == "playback.media-streaming.soundcloud.cloud"):
        raise RuntimeError("SoundCloud returned an unsupported media host. Playback was blocked.")
    return normalize(info), stream
