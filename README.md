# Ember

Lightweight native Linux YouTube Music player with the existing GTK UI and timed lyrics. Music plays directly through GStreamer; no separate music player or browser is required.

## Open

On this PC, search **Ember** in your app menu. The installed copy lives in `~/.local/share/ember-player/`; source changes must be copied there to update it. Settings remain in `~/.config/ember-player/`.

From this checkout: `bash run.sh`. Requires system Python/PyGObject, GTK4, Pycairo, GStreamer playback/codec plugins, Node.js (for yt-dlp JavaScript support), and uv. Requirements install into a system-site-packages venv. If updating an existing venv, run `uv pip install --python .venv/bin/python -r requirements.txt`.

## Music

Search by song or artist and press Enter. Search uses YouTube Music's Songs category rather than general video results; this helps avoid full albums and unrelated videos, but recordings can still be mislabeled. Double-click a song to play; the result list becomes the queue. Public/unlisted YouTube Music and YouTube playlist links are supported, including pagination for large playlists. A watch link opens its selected track even when it includes a playlist parameter.

Hearts save tracks locally. YouTube likes use their own private `youtube-library.json`; older SoundCloud settings and likes are preserved and are not reused as YouTube IDs. Queue, shuffle/repeat, seek, volume and MPRIS remain available.

No Google login, private/account library, personalized recommendations or music radio is implemented. Transfer Spotify playlists through TuneMyMusic/Soundiiz (links in Settings), review matches, make the destination YouTube Music playlist public/unlisted, then paste its link in Search. Transfer fees/limits and completeness are not verified.

This uses unofficial ytmusicapi and yt-dlp integrations. Availability, regional/age/account restrictions and upstream changes can interrupt playback. No subscription/DRM bypass or universal ad-free guarantee is provided. yt-dlp resolves temporary media URLs only when playing; they are not stored in likes or logged by Ember.

## Lyrics

LRCLIB exact metadata lookup supplies line-synced/plain lyrics where available. **Lyrics → Match lyrics** can load Spicy Lyrics word timing from a Spotify link for the exact same recording; this is lyrics-only and needs your provider key in Settings. Local TTML/LRC imports are also available. Timing/coverage depends on source; no word timing is invented. Credits remain visible. SoundCloud/Spotify playback files are retained as unused prototypes.

## Verification

`.venv/bin/python -m unittest -q test_core.py test_music.py test_soundcloud.py`

21 tests passed on2026-11-04. Separate live verification: YouTube Music song search returned20 results; Tycho Awake resolved, decoded in GStreamer to a synchronized silent sink and advanced7.79sec; seek request accepted. GTK startup, Search and Settings smoke passed with isolated config. These checks do not prove audible speaker output, post-seek position, private account access, playlist coverage or RAM usage. No deployment.

## Spotify playlist transfer

See [TRANSFER.md](TRANSFER.md) for the local CSV matching/review/upload helper without a transfer-service song cap.
