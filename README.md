# Ember

Ember is a native Linux desktop YouTube Music player written in Rust with GTK4 and GStreamer, plus a private Python music bridge and a native SwiftUI iPhone client that shares playlists through a small authenticated backend.

## Desktop

From this checkout:

```bash
bash run.sh
```

`run.sh` creates `.venv` with uv, installs the Python bridge dependencies, builds `desktop-rust` in release mode and launches it. Re-running it rebuilds incrementally and launches the current source. See [desktop-rust/README.md](desktop-rust/README.md) for requirements, layout and limitations.

The desktop client provides public YouTube Music search, artwork, a persistent queue with independent duplicate occurrences, synchronized LRCLIB lyrics, a bounded automatic audio cache and Ember-owned playlist sync through the mobile backend. It uses the checkout's Python core only for catalog search, stream resolution, audio downloads and lyrics. Public recordings need no account. If `~/.config/ember/cookies.txt` exists — or `EMBER_YOUTUBE_COOKIES` names another file — the desktop hands those exported YouTube cookies to yt-dlp, which is what lets the recordings the account can see (age-restricted ones, mostly) resolve here the way they do on the server.

## Layout

- `desktop-rust/` — GTK4/Rust desktop client, embedded stylesheet, music bridge and shared-library client.
- `core/` — the shared music and storage package: public YouTube Music search, playlist loading and yt-dlp stream resolution (`core/music.py`), the private file-backed audio cache and offline downloads (`core/audio_cache.py`), the bounded artwork and playlist-metadata cache (`core/disk_cache.py`), private JSON storage helpers (`core/services.py`) and the local playlist filtering used by the mobile API (`core/playlist_search.py`).
- `tools/` — CSV import and match-report scripts.
- `tests/` — shared-core test suite.
- `mobile/` — SwiftUI iPhone app and the authenticated backend for search, prepared M4A audio, lyrics and shared playlists.
- `tools/import_ember_playlist.py` / [transfer docs](docs/transfer.md) — import approved Spotify CSV matches into the shared Ember library, preserving order and duplicates. `tools/transfer_playlist.py` prepares match reports and retains the separate YouTube upload option.
- [Playlist sync plan](docs/playlist-sync-plan.md) — shared-playlist architecture and rollout status.

The GTK Python desktop UI and the abandoned librespot engine prototype have been removed. Rust is the desktop application; the remaining Python modules are the shared music and storage core.

## iPhone

See [mobile/README.md](mobile/README.md). The backend is a standard-library HTTP service with bearer authentication; the phone stores its access key in the Keychain. Shared playlists are documented in [mobile/backend/LIBRARY_API.md](mobile/backend/LIBRARY_API.md).

## Verification

CI runs the Linux suites from [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

```bash
python -m unittest discover -q
cd desktop-rust && python -m unittest -q test_bridge.py test_cache.py test_library.py
cd desktop-rust && cargo test --locked
```

The iOS app is built by the manual [`ios-unsigned.yml`](.github/workflows/ios-unsigned.yml) workflow on macOS; it cannot be compiled on Linux.

## Limits

This uses unofficial ytmusicapi and yt-dlp integrations. Availability, regional/age/account restrictions and upstream changes can interrupt playback; no subscription or DRM bypass is provided. Playback resolves temporary public stream URLs only when streaming, and neither those URLs nor credentials are stored by Ember. Persistent offline copies may conflict with YouTube's terms or copyright; use downloads only for content you have the right to keep.

The Rust desktop client publishes MPRIS media controls but does not implement Google account sync. Shared playlists are stored by the self-hosted backend, not by a provider account. The iPhone app has its own cache and playback limitations documented in [mobile/README.md](mobile/README.md).

Last.fm scrobbling is available through the shared backend; see [Last.fm setup](mobile/backend/LASTFM.md).
