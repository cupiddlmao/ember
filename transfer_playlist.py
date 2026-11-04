"""Spotify CSV -> reviewed YouTube Music playlist; no service-imposed song cap."""
import argparse
import csv
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import time
import unicodedata

from ytmusicapi import YTMusic
from services import save_private


def clean(value):
    return re.sub(r'[^a-z0-9]+', ' ', unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode().lower()).strip()


def read_tracks(path):
    with path.open(encoding='utf-8-sig', newline='') as source:
        reader = csv.DictReader(source)
        tracks = []
        for row in reader:
            fields = {clean(key): value for key, value in row.items() if key}
            title = fields.get('track name') or fields.get('name') or fields.get('title')
            artist = fields.get('artist name s') or fields.get('artist name') or fields.get('artists') or fields.get('artist')
            if not title or not artist:
                raise ValueError('CSV needs Track Name and Artist Name(s) columns (or title/artist).')
            tracks.append({'title': title, 'artist': artist, 'duration_ms': fields.get('duration ms', '')})
        if not tracks:
            raise ValueError('CSV contains no tracks.')
        return tracks


def score(track, candidate):
    title, match = clean(track['title']), clean(candidate.get('title', ''))
    # Preserve recording qualifiers: never quietly match an original to a remix/cover.
    for word in ('remix', 'cover', 'live', 'karaoke', 'instrumental', 'sped', 'slowed'):
        if (word in title.split()) != (word in match.split()):
            return 0
    artist = clean(track['artist'].split(';')[0])
    names = [clean(a.get('name', '')) for a in candidate.get('artists', [])]
    title_score = SequenceMatcher(None, title, match).ratio()
    artist_score = max((SequenceMatcher(None, artist, name).ratio() for name in names), default=0)
    if title_score < .8 or artist_score < .8:
        return 0
    duration = candidate.get('duration_seconds')
    if track.get('duration_ms') and duration:
        if abs(float(track['duration_ms']) / 1000 - duration) > 10:
            return 0
    return round((title_score + artist_score) / 2, 3)


def match_tracks(source, report, client):
    tracks = read_tracks(source)
    fingerprint = hashlib.sha256(source.read_bytes()).hexdigest()
    data = json.loads(report.read_text()) if report.exists() else {'source_sha256': fingerprint, 'matches': []}
    if data.get('source_sha256') != fingerprint:
        raise ValueError('This report belongs to a different CSV; choose a new report filename.')
    for track in tracks[len(data['matches']):]:
        candidates = client.search(track['title'] + ' ' + track['artist'], filter='songs', limit=5)
        candidates = [c for c in candidates if re.fullmatch(r'[\w-]{11}', c.get('videoId') or '', flags=re.ASCII)]
        best = max(candidates, key=lambda c: score(track, c), default=None)
        value = score(track, best) if best else 0
        data['matches'].append({**track, 'score': value, 'video_id': best['videoId'] if value else '', 'matched_title': best.get('title', '') if best else '', 'matched_artists': [a.get('name', '') for a in best.get('artists', [])] if best else [], 'approved': False})
        save_private(report, data)
        print(f"Matched {len(data['matches'])}/{len(tracks)}", flush=True)
        time.sleep(.4)
    return data


def upload(report, state_path, title, client):
    data = json.loads(report.read_text())
    ids = list(dict.fromkeys(row['video_id'] for row in data['matches'] if row.get('approved') is True and row.get('video_id')))
    if not ids:
        raise ValueError('No approved matches. Review the report and set approved to true for selected songs.')
    if any(not re.fullmatch(r'[\w-]{11}', value, flags=re.ASCII) for value in ids):
        raise ValueError('Invalid video ID in review report.')
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    if state.get('creating'):
        raise RuntimeError('Playlist creation outcome is uncertain. Check your account before retrying; put the created playlist ID in the state file and remove creating.')
    if not state.get('playlist_id'):
        save_private(state_path, {'creating': True})
        playlist = client.create_playlist(title, 'Transferred locally from Spotify with Ember.', privacy_status='UNLISTED')
        if not isinstance(playlist, str):
            raise RuntimeError('Playlist creation did not return an ID. Check your account before retrying.')
        state = {'playlist_id': playlist}
        save_private(state_path, state)
    playlist = state['playlist_id']
    # Read remote contents on every run to reconcile interrupted batch writes.
    existing = client.get_playlist(playlist, limit=None)
    if len(existing.get('tracks', [])) < existing.get('trackCount', 0):
        raise RuntimeError('Could not read the entire destination playlist; refusing an unsafe resume.')
    present = {item.get('videoId') for item in existing.get('tracks', [])}
    remaining = [video for video in ids if video not in present]
    for start in range(0, len(remaining), 50):
        result = client.add_playlist_items(playlist, remaining[start:start + 50])
        status = result.get('status', '') if isinstance(result, dict) else result
        if not isinstance(status, str) or 'SUCCEEDED' not in status:
            raise RuntimeError('A batch failed or had an uncertain result. Rerun to reconcile playlist contents before continuing.')
        print(f"Added {min(start + 50, len(remaining))}/{len(remaining)}", flush=True)
        time.sleep(1)
    print('https://music.youtube.com/playlist?list=' + playlist)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    match = sub.add_parser('match', help='Read CSV and save a resumable review report; no login needed')
    match.add_argument('csv', type=Path)
    match.add_argument('report', type=Path)
    send = sub.add_parser('upload', help='Create an unlisted playlist from explicitly approved matches')
    send.add_argument('report', type=Path)
    send.add_argument('--auth', type=Path, required=True)
    send.add_argument('--title', required=True)
    send.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == 'match':
            match_tracks(args.csv, args.report, YTMusic())
        else:
            upload(args.report, args.state, args.title, YTMusic(str(args.auth)))
    except Exception as error:
        # API errors can contain authentication headers; never print their raw text.
        if isinstance(error, (ValueError, RuntimeError)):
            parser.exit(1, str(error) + '\n')
        parser.exit(1, 'Transfer stopped. Check network/authentication; saved progress can be resumed.\n')


if __name__ == '__main__':
    main()
