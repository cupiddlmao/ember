"""Public YouTube Music catalog with native audio resolved by yt-dlp."""
import re
import urllib.parse
import requests
import hashlib
from pathlib import Path
import disk_cache
from services import load_private

import yt_dlp
from ytmusicapi import YTMusic
from youtube_auth import client as authenticated_client
from youtube_auth import OAUTH_FILE


def _google_get(client, endpoint, **params):
    response = requests.get('https://www.googleapis.com/youtube/v3/' + endpoint,
                            params=params, headers={'Authorization': client.headers['authorization']}, timeout=15)
    if response.status_code != 200:
        raise RuntimeError('Google could not read playlists. Check YouTube Data API access or reconnect your account.')
    return response.json()


def _google_pages(client, endpoint, **params):
    items, token = [], None
    while True:
        data = _google_get(client, endpoint, maxResults=50, **params, **({'pageToken': token} if token else {}))
        items.extend(data.get('items', []))
        next_token = data.get('nextPageToken')
        if not next_token:
            return items
        if next_token == token:
            raise RuntimeError('Google repeated a playlist page. Retry loading this list.')
        token = next_token


def _google_images(snippet):
    return list((snippet.get('thumbnails') or {}).values())


def _google_playlist(client, playlist):
    metadata = _google_get(client, 'playlists', part='snippet', id=playlist).get('items', [])
    if not metadata:
        raise RuntimeError('This playlist is unavailable to the connected Google account.')
    snippet = metadata[0]['snippet']
    entries = _google_pages(client, 'playlistItems', part='snippet,contentDetails', playlistId=playlist)
    ids = [item.get('contentDetails', {}).get('videoId') for item in entries]
    details = {}
    unique = list(dict.fromkeys(v for v in ids if v))
    for start in range(0, len(unique), 50):
        data = _google_get(client, 'videos', part='snippet,contentDetails', id=','.join(unique[start:start + 50]))
        details.update({item['id']: item for item in data.get('items', [])})
    tracks = []
    for video in ids:
        item = details.get(video)
        if not item:
            continue
        info = item['snippet']
        duration = item.get('contentDetails', {}).get('duration', '')
        match = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
        seconds = sum(int(n or 0) * scale for n, scale in zip(match.groups(), (3600, 60, 1))) if match else 0
        tracks.append(normalize({'videoId': video, 'title': info['title'], 'artists': [{'name': info.get('channelTitle', 'Unknown artist')}],
                                 'duration_seconds': seconds, 'thumbnails': _google_images(info)}))
    meta = _playlist_meta(playlist, {'title': snippet['title'], 'author': snippet.get('channelTitle'), 'thumbnails': _google_images(snippet)})
    meta['track_count'] = len(tracks)
    return {'kind': 'playlist', 'title': meta['title'], 'playlist': meta, 'tracks': tracks, 'returned': len(entries), 'unavailable': len(entries)-len(tracks)}


class Quiet:
    def debug(self, *_): pass
    def warning(self, *_): pass
    def error(self, *_): pass


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


def normalize(item):
    video = item.get('videoId') or item.get('id', '')
    if not re.fullmatch(r'[\w-]{11}', str(video), flags=re.ASCII):
        raise ValueError('YouTube returned an invalid track ID.')
    duration = item.get('duration_seconds') or item.get('duration') or 0
    if isinstance(duration, str):
        parts = duration.split(':')
        duration = sum(int(part) * 60 ** i for i, part in enumerate(reversed(parts)))
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
        'duration_ms': round(float(duration) * 1000),
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


def _search(query, auth=None):
    client = authenticated_client(auth) if auth else YTMusic()
    if query.startswith(('https://', 'http://')):
        video, playlist = music_link(query)
        if playlist and not video:
            if auth and str(auth) == str(OAUTH_FILE):
                return _google_playlist(client, playlist)
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


def resolve(track):
    video, _playlist = music_link(track['uri'])
    if not video:
        raise ValueError('Select a song to play.')
    options = {'quiet': True, 'logger': Quiet(), 'no_warnings': True, 'noplaylist': True, 'socket_timeout': 15, 'retries': 1, 'extractor_retries': 1, 'format': 'bestaudio[protocol=https]/bestaudio', 'js_runtimes': {'node': {}}}
    try:
        with yt_dlp.YoutubeDL(options) as client:
            info = client.extract_info('https://www.youtube.com/watch?v=' + video, download=False)
    except yt_dlp.utils.DownloadError:
        raise RuntimeError('YouTube could not provide this audio. It may be unavailable, region/age restricted, or require sign-in. Try another recording.') from None
    stream = info.get('url', '')
    parsed = urllib.parse.urlsplit(stream)
    if info.get('has_drm') or parsed.scheme != 'https' or not (parsed.hostname or '').endswith('.googlevideo.com'):
        raise RuntimeError('YouTube returned an unsupported or restricted audio stream.')
    return normalize(info), stream


def download_audio(track, folder, progress=None):
    """Download an audio-only copy into a private temporary folder.

    Returns ``(normalized_track, file_path)``. The caller owns the folder and
    is responsible for moving the file into its permanent or cached location.
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
    options = {
        'quiet': True, 'logger': Quiet(), 'no_warnings': True, 'noplaylist': True,
        'socket_timeout': 30, 'retries': 1, 'extractor_retries': 1, 'noprogress': True,
        'format': 'bestaudio[protocol=https]/bestaudio', 'overwrites': True,
        'outtmpl': str(folder / (video + '.%(ext)s')), 'js_runtimes': {'node': {}},
    }
    if hooks:
        options['progress_hooks'] = hooks
    try:
        with yt_dlp.YoutubeDL(options) as client:
            info = client.extract_info('https://www.youtube.com/watch?v=' + video, download=True)
    except yt_dlp.utils.DownloadError:
        raise RuntimeError('YouTube could not provide this audio for download. It may be unavailable, region/age restricted, or require sign-in.') from None
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
    return normalize(info), source


def _library_playlists(auth):
    """Read account playlists without exposing login or account details."""
    try:
        client = authenticated_client(auth)
        if str(auth) == str(OAUTH_FILE):
            entries = _google_pages(client, 'playlists', part='snippet,contentDetails', mine='true')
            return [_playlist_meta(item['id'], {'title': item['snippet']['title'], 'author': item['snippet'].get('channelTitle'),
                    'thumbnails': _google_images(item['snippet'])}) for item in entries]
        if str(auth) != str(OAUTH_FILE) and not client.get_account_info().get('accountName'):
            raise ValueError('Account validation failed')
        entries = client.get_library_playlists(limit=None)
        return [_playlist_meta(item['playlistId'], item) for item in entries
                if re.fullmatch(r'[\w-]{10,200}', item.get('playlistId') or '', flags=re.ASCII)]
    except Exception:
        raise RuntimeError('YouTube library login expired or the service is unavailable. Renew local login and retry.') from None


def _cache_key(value, auth=None):
    scope = 'public'
    if auth:
        try:
            data = load_private(Path(auth))
            identity = data.get('refresh_token') or data.get('cookie')
            if not identity:
                return None
            scope = hashlib.sha256(identity.encode()).hexdigest()
        except (OSError, ValueError, TypeError):
            return None
    return scope + ':' + value


def cached_playlist(query, auth=None):
    key = _cache_key(query, auth)
    return disk_cache.get_metadata(key) if key else None


def search(query, auth=None):
    epoch = disk_cache.generation()
    key = _cache_key(query, auth)
    result = _search(query, auth)
    if result.get('kind') == 'playlist':
        if key:
            disk_cache.put_metadata(key, result, epoch)
    return result


def cached_library(auth):
    return cached_playlist('account-library', auth)


def library_playlists(auth):
    epoch = disk_cache.generation()
    key = _cache_key('account-library', auth)
    result = _library_playlists(auth)
    if key:
        disk_cache.put_metadata(key, result, epoch)
    return result
