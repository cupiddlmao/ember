"""Public YouTube Music catalog with native audio resolved by yt-dlp."""
import re
import urllib.parse

import yt_dlp
from ytmusicapi import YTMusic


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
    return {'source': 'youtube', 'type': 'track', 'id': str(video), 'uri': 'https://music.youtube.com/watch?v=' + str(video), 'name': item.get('track') or item.get('title') or 'Untitled', 'artists': [{'name': (artist.get('name') if isinstance(artist, dict) else artist) or 'Unknown artist'} for artist in artists], 'album': {'name': (item.get('album') or {}).get('name', '') if isinstance(item.get('album'), dict) else item.get('album', ''), 'images': images}, 'duration_ms': round(float(duration) * 1000)}


def search(query):
    client = YTMusic()
    if query.startswith(('https://', 'http://')):
        video, playlist = music_link(query)
        if playlist and not video:
            entries = client.get_playlist(playlist, limit=None).get('tracks', [])
        else:
            return [resolve({'uri': 'https://music.youtube.com/watch?v=' + video})[0]]
    else:
        entries = client.search(query, filter='songs', limit=20)[:20]
    return [normalize(item) for item in entries if item.get('videoId') and item.get('isAvailable', True)]


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
