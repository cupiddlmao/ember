# Ember

Lightweight native Linux YouTube Music player with the existing GTK UI and timed lyrics. Music plays directly through GStreamer; no separate music player or browser is required.

## Open

On this PC, search **Ember** in your app menu. The installed copy lives in `~/.local/share/ember-player/`; source changes must be copied there to update it. Settings remain in `~/.config/ember-player/`.

From this checkout: `bash run.sh`. Requires system Python/PyGObject, GTK4, Pycairo, GStreamer playback/codec plugins, Node.js (for yt-dlp JavaScript support), and uv. Requirements install into a system-site-packages venv. If updating an existing venv, run `uv pip install --python .venv/bin/python -r requirements.txt`.

## Music

Search by song or artist and press Enter. Search uses YouTube Music's Songs category rather than general video results; this helps avoid full albums and unrelated videos, but recordings can still be mislabeled. Double-click a song to play; the result list becomes the queue. Public/unlisted YouTube Music and YouTube playlist links are supported; the full public playlist is requested, not a first page. A watch link opens its selected track even when it includes a playlist parameter.

Pasting a playlist link opens a playlist page with its title, artwork, owner when supplied, playable-track count, Play, Shuffle, and a local save action. Saving stores only id, canonical public URL, title, owner, artwork URLs and a count; media stream URLs and tracks are never stored. Saved playlists reappear in the sidebar and Home after restart and are clearly local, not Google account library sync.

The playlist page also has **Set cover** for choosing a local image. Ember scales it to a 512 px PNG and stores it privately under `$XDG_CONFIG_HOME/ember-player/playlist-covers/`; the custom image replaces the YouTube artwork in the playlist header, sidebar and Home even when the catalog image is unavailable. **Reset cover** returns to YouTube artwork. Custom covers stay on this device and are never included in saved playlist metadata.

Resized artwork and playlist/account metadata are cached in the user's XDG cache. Artwork is bounded to 200 MiB, metadata to 20 MiB and seven days; account metadata uses a separate key per login. A cached playlist stays visible if refresh fails. If a YouTube thumbnail host fails, Ember retries the equivalent image on `img.youtube.com`.

Audio uses a separate file-backed cache. Explicit downloads live under `$XDG_DATA_HOME/ember-player/downloads`, appear in the Downloads page, and stay until you remove them. The automatic cache lives under `$XDG_CACHE_HOME/ember-player/audio`, defaults to 2 GiB (configurable in Settings), evicts least-recently-used tracks, and can prefetch the next queue track. Cached playback uses the local file and works offline. Settings → Clear cache removes automatic audio and metadata/artwork but never explicit downloads, likes, saved playlists or login. Stream URLs and credentials are never stored in either audio store.

Hearts save tracks locally. YouTube likes use their own private `youtube-library.json`; older SoundCloud settings and likes are preserved and are not reused as YouTube IDs. Queue, shuffle/repeat, seek, volume and MPRIS remain available.

YouTube account playlists can be read using the private browser-login file created by `import_youtube_auth.py` (see [TRANSFER.md](TRANSFER.md)). With that file present, Ember loads the account library on startup; use **Refresh YouTube playlists** to reload it. Owned account playlists appear alongside local saved playlists. OAuth uses the official YouTube Data API for owned/private playlist access; playlists saved from other creators can be opened by public link instead. Local hearts and bookmarks remain local; Ember does not change your Google library. Personalized recommendations and music radio are not implemented. Native audio still uses public resolution and does not use account cookies to unlock restricted streams.

For browser-based Google authorization without copying request headers, use **Settings → Sign in with Google**. A one-time Google Cloud TV/limited-input OAuth client setup is required; see [GOOGLE_LOGIN.md](GOOGLE_LOGIN.md). Tokens remain local and OAuth takes precedence over the older browser-header file. Live OAuth approval/refresh has not yet been verified with real client credentials. To transfer Spotify playlists, use the local CSV matching/review/upload helper documented in [TRANSFER.md](TRANSFER.md); Ember itself does not upload playlists.

Last.fm scrobbling is optional and starts disconnected. In **Settings → Last.fm scrobbling**, create a Last.fm API account, paste its API key and shared secret, choose **Connect Last.fm**, approve Ember in the browser, then choose **Complete connection**. Ember sends now-playing updates when a track starts and scrobbles it once Last.fm's listening threshold is reached. The shared secret, authorization token and session key are stored privately in `$XDG_CONFIG_HOME/ember-player/lastfm.json`; **Disconnect** clears the session and stops scrobbling. API errors appear in the status bar. Live Last.fm authentication was not exercised in this pass because no API credentials were provided.

This uses unofficial ytmusicapi and yt-dlp integrations. Availability, regional/age/account restrictions and upstream changes can interrupt playback. No subscription/DRM bypass or universal ad-free guarantee is provided. yt-dlp resolves temporary media URLs only when streaming; they are not stored in likes or logged by Ember. Persistent offline copies of YouTube audio may conflict with YouTube's terms or copyright; this is a local, user-initiated feature, and the repository does not include media or credentials. Use it only with content you have the right to download.

## Lyrics

LRCLIB exact metadata lookup runs automatically when a track starts and supplies line-synced/plain lyrics where available. There are no manual import or provider-match buttons in the lyrics header. Timing/coverage depends on the provider's metadata for that recording; no timing is invented. Credits remain visible. SoundCloud/Spotify playback files are retained as unused prototypes.

## Verification

Latest pass: Last.fm desktop auth/scrobbling, custom playlist covers, alternate YouTube artwork delivery and the earlier caching/UI fixes are covered by focused tests. No live download, audible playback, RAM benchmark or live Last.fm account test was performed in this pass.

`.venv/bin/python -m unittest -q test_core.py test_music.py test_artwork.py test_playlists.py test_mpris.py test_generations.py test_disk_cache.py test_app_regressions.py test_audio_cache.py test_playlist_covers.py test_lastfm.py`

61 focused tests passed on 2026-11-05. They cover audio-store writes, private permissions, explicit downloads, automatic-cache bounds and LRU eviction, promotion without redownloading, startup cleanup of valid and stale rows, cache-first playback bypassing the resolver, cache expiry/account separation, artwork reuse, cached-art delivery before parenting, alternate-host download fallback, cached-playlist refresh fallback, independent request generations, playlist metadata, custom playlist cover storage/path validation, cover-file save handling, Last.fm signatures, signed POST transport, token/session requests, scrobble thresholds, Last.fm private storage, artwork host validation, MPRIS paths and app regressions. An isolated native GTK startup check stayed alive under temporary XDG directories, and the installed app was restarted with MPRIS registered. The custom-cover file picker was not clicked through in the live window, and no screenshot or render check was performed this pass. Earlier render checks at 1280×820 and 980×640 covered Home/search/playlist with local metadata and a public static thumbnail. Earlier live verification (not repeated after this pass): YouTube Music song search returned 20 results; Tycho Awake resolved, decoded in GStreamer to a synchronized silent sink and advanced 7.79 sec; seek request accepted. These checks do not prove audible speaker output, post-seek position, private account access, live playlist coverage or RAM usage. No deployment.

## Spotify playlist transfer

See [TRANSFER.md](TRANSFER.md) for the local CSV matching/review/upload helper without a transfer-service song cap.
