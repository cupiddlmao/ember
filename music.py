"""Public YouTube Music catalog for the Rust desktop bridge and mobile API."""
import os
import re
import urllib.parse
from pathlib import Path

import disk_cache

import yt_dlp
from ytmusicapi import YTMusic


class Quiet:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


# Age-restricted and sign-in-gated recordings come back with no audio-only
# stream at all: for those, YouTube serves only muxed (video + audio) formats
# over https, plus HLS manifests the players here cannot stream. A selector
# that ends at `bestaudio` therefore fails on exactly the tracks the account
# cookies were added for, with "requested format is not available". The muxed
# clause comes last, so an ordinary track still resolves to its best audio-only
# stream and only a recording with nothing audio-only falls back to format 18.
# Every clause is https-only for the same reason: a bare `bestaudio` also matches
# an m3u8 manifest, and on a gated recording that manifest outranked the muxed
# format the players can play, so the stream broke after playback had started.
AUDIO_FORMAT = 'bestaudio[protocol=https]/best[acodec!=none][protocol=https]'


def music_link(value):
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != 'https' or parsed.netloc not in ('music.youtube.com', 'www.youtube.com', 'youtube.com', 'youtu.be'):
        raise ValueError('Use an HTTPS YouTube Music track or public playlist link.')
    query = urllib.parse.parse_qs(parsed.query)
    video = parsed.path.lstrip('/') if parsed.netloc == 'youtu.be' else query.get('v', [''])[0]
    playlist = query.get('list', [''])[0]
    if video and not re.fullmatch(r'[\w-]{11}', video, flags=re.ASCII):
        raise ValueError('Invalid YouTube video ID.')
    if playlist and not re.fullmatch(r'[\w-]{10,200}', playlist, flags=re.ASCII):
        raise ValueError('Invalid YouTube playlist ID.')
    if not video and not playlist:
        raise ValueError('Paste a track or playlist link, not a channel or home page.')
    return video, playlist


def _artist_name(value):
    name = str(value or '').strip()
    if name.lower().endswith(' - topic'):
        name = name[:-8].rstrip()
    return name or 'Unknown artist'


def _duration_seconds(value):
    """Length in seconds, or 0 when the provider gave no usable number.

    ytmusicapi puts the word "LIVE" where the duration text would be for a live
    recording. int() on that raised, and _available() counts anything normalize()
    rejects as unavailable, so live recordings silently disappeared from search
    and playlist results instead of showing without a length.
    """
    if isinstance(value, str):
        try:
            return sum(int(part) * 60 ** i for i, part in enumerate(reversed(value.split(':'))))
        except ValueError:
            return 0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def normalize(item):
    video = item.get('videoId') or item.get('id', '')
    if not re.fullmatch(r'[\w-]{11}', str(video), flags=re.ASCII):
        raise ValueError('YouTube returned an invalid track ID.')
    duration = _duration_seconds(item.get('duration_seconds') or item.get('duration') or item.get('length') or 0)
    artists = item.get('artists') or [{'name': item.get('artist') or item.get('uploader') or 'Unknown artist'}]
    images = item.get('thumbnails') or ([{'url': item['thumbnail']}] if item.get('thumbnail') else [])
    if not images:
        images = [{'url': 'https://img.youtube.com/vi/' + str(video) + '/mqdefault.jpg'}]
    return {
        'source': 'youtube',
        'type': 'track',
        'id': str(video),
        'uri': 'https://music.youtube.com/watch?v=' + str(video),
        'name': item.get('track') or item.get('title') or 'Untitled',
        'artists': [{'name': _artist_name(artist.get('name') if isinstance(artist, dict) else artist)} for artist in artists],
        'album': {
            'name': (item.get('album') or {}).get('name', '') if isinstance(item.get('album'), dict) else item.get('album', ''),
            'images': images,
        },
        'duration_ms': round(duration * 1000),
    }


def _images(values):
    result = []
    for item in values or []:
        if isinstance(item, str):
            url = item
        elif isinstance(item, dict):
            url = item.get('url')
        else:
            continue
        if not isinstance(url, str) or urllib.parse.urlsplit(url).scheme != 'https':
            continue
        image = {'url': url}
        for key in ('width', 'height'):
            try:
                value = int(item.get(key)) if isinstance(item, dict) else 0
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                image[key] = value
        result.append(image)
    return result


def _owner(data):
    owner = data.get('author') if isinstance(data, dict) else None
    if owner is None and isinstance(data, dict):
        owner = data.get('owner')
    if isinstance(owner, dict):
        return str(owner.get('name') or '')
    return str(owner or '')


def _playlist_meta(playlist_id, data):
    data = data or {}
    return {
        'id': playlist_id,
        'title': str(data.get('title') or 'YouTube Music playlist').strip(),
        'owner': _owner(data).strip(),
        'uri': 'https://music.youtube.com/playlist?list=' + playlist_id,
        'images': _images(data.get('thumbnails') or data.get('images')),
    }


def _available(entries):
    tracks, unavailable = [], 0
    for item in entries or []:
        if not item or not item.get('videoId') or item.get('isAvailable') is False:
            unavailable += 1
            continue
        try:
            tracks.append(normalize(item))
        except (TypeError, ValueError):
            unavailable += 1
    return tracks, unavailable


def _search(query):
    client = YTMusic()
    if query.startswith(('https://', 'http://')):
        video, playlist = music_link(query)
        if video and playlist == 'RDAMVM' + video:
            data = client.get_watch_playlist(videoId=video, radio=True, limit=25) or {}
            entries = (data.get('tracks') or [])[:25]
            tracks, unavailable = _available(entries)
            return {'kind': 'radio', 'title': 'Song radio', 'tracks': tracks,
                    'returned': len(entries), 'unavailable': unavailable}
        if playlist and not video:
            data = client.get_playlist(playlist, limit=None) or {}
            entries = data.get('tracks') or []
            tracks, unavailable = _available(entries)
            meta = _playlist_meta(playlist, data)
            meta['track_count'] = len(tracks)
            return {
                'kind': 'playlist',
                'title': meta['title'],
                'playlist': meta,
                'tracks': tracks,
                'returned': len(entries),
                'unavailable': unavailable,
            }
        if video:
            track = resolve({'uri': 'https://music.youtube.com/watch?v=' + video})[0]
            return {'kind': 'track', 'title': track['name'], 'tracks': [track], 'returned': 1, 'unavailable': 0}
    entries = (client.search(query, filter='songs', limit=20) or [])[:20]
    tracks, unavailable = _available(entries)
    return {'kind': 'search', 'title': 'Search results', 'tracks': tracks, 'returned': len(entries), 'unavailable': unavailable}


def _ytdlp_options(**overrides):
    """Shared yt-dlp options for stream resolution and audio download.

    EMBER_YOUTUBE_COOKIES, when set to a Netscape cookies.txt exported from a
    signed-in YouTube account, lets yt-dlp fetch recordings that would
    otherwise be blocked for being age-restricted or sign-in-gated. Without
    it the fetch stays anonymous.
    """
    options = {'quiet': True, 'logger': Quiet(), 'no_warnings': True, 'noplaylist': True}
    cookie_file = os.environ.get('EMBER_YOUTUBE_COOKIES', '').strip()
    if cookie_file:
        options['cookiefile'] = cookie_file
    options.update(overrides)
    return options


def _extract(options, video, download, refusal):
    """Run one yt-dlp extraction, reporting every failure as ``refusal``.

    yt-dlp raises DownloadError for a refused recording, but it re-raises its own
    YoutubeDLError subclasses out of extraction unchanged (a cookiefile it cannot
    load, a cancelled download), and those reached callers as crashes instead of
    a failure they can classify. Its close step also rewrites the cookies file, so
    a read-only deployment path raised PermissionError after the stream had
    already resolved; that is not a refusal, and throwing playable audio away for
    it is wrong.
    """
    info = None
    try:
        with yt_dlp.YoutubeDL(options) as client:
            info = client.extract_info('https://www.youtube.com/watch?v=' + video, download=download)
    except yt_dlp.utils.YoutubeDLError:
        raise RuntimeError(refusal) from None
    except OSError:
        if info is None:
            raise RuntimeError(refusal) from None
    return info


def resolve(track):
    video, _playlist = music_link(track['uri'])
    if not video:
        raise ValueError('Select a song to play.')
    options = _ytdlp_options(socket_timeout=15, retries=1, extractor_retries=1, format=AUDIO_FORMAT, js_runtimes={'node': {}})
    info = _extract(options, video, False, 'YouTube could not provide this audio. It may be unavailable, region/age restricted, or require sign-in. Try another recording.')
    stream = info.get('url', '')
    parsed = urllib.parse.urlsplit(stream)
    if info.get('has_drm') or parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.googlevideo.com'):
        raise RuntimeError('YouTube returned an unsupported or restricted audio stream.')
    return normalize(info), stream


def download_audio(track, folder, progress=None, max_bytes=None):
    """Download an audio-only copy into a private temporary folder.

    Returns ``(normalized_track, file_path)``. The caller owns the folder and
    is responsible for moving the file into its permanent or cached location.
    ``max_bytes`` makes yt-dlp refuse, or abort mid-transfer, a larger stream so an
    unexpectedly long recording cannot fill the disk before any size check runs.
    """
    video, _playlist = music_link(track['uri'])
    if not video:
        raise ValueError('Select a song to download.')
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    hooks = []
    if progress:
        def report(data):
            if data.get('status') == 'downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
                if total:
                    progress(min(1, max(0, data.get('downloaded_bytes', 0) / total)))
        hooks.append(report)
    options = _ytdlp_options(
        socket_timeout=30, retries=1, extractor_retries=1, noprogress=True,
        format=AUDIO_FORMAT, overwrites=True,
        outtmpl=str(folder / (video + '.%(ext)s')), js_runtimes={'node': {}},
    )
    if hooks:
        options['progress_hooks'] = hooks
    if max_bytes:
        options['max_filesize'] = int(max_bytes)
    info = _extract(options, video, True, 'YouTube could not provide this audio for download. It may be unavailable, region/age restricted, or require sign-in.')
    if not isinstance(info, dict):
        raise RuntimeError('YouTube returned an invalid audio result.')
    if info.get('has_drm'):
        raise RuntimeError('YouTube returned a restricted audio stream.')
    candidates = [path for path in folder.iterdir()
                  if path.is_file() and path.name.startswith(video)
                  and path.suffix not in ('.part', '.ytdl')]
    if not candidates:
        raise RuntimeError('YouTube did not produce a downloadable audio file.')
    source = max(candidates, key=lambda path: path.stat().st_size)
    if source.stat().st_size <= 0:
        raise RuntimeError('YouTube returned an empty audio file.')
    if max_bytes and source.stat().st_size > max_bytes:
        source.unlink(missing_ok=True)
        raise RuntimeError('This recording is larger than the allowed audio size.')
    return normalize(info), source


def _cache_key(value):
    return 'public:' + value


def cached_playlist(query):
    return disk_cache.get_metadata(_cache_key(query))


def search(query):
    epoch = disk_cache.generation()
    key = _cache_key(query)
    result = _search(query)
    if result.get('kind') == 'playlist':
        disk_cache.put_metadata(key, result, epoch)
    return result
