# Ember

Ember is a native Linux desktop YouTube Music player written in Rust with GTK4 and GStreamer, plus a private Python music bridge and a native SwiftUI iPhone client that shares playlists through a small authenticated backend.

## Desktop

From this checkout:

```bash
bash run.sh
```

`run.sh` creates `.venv` with uv, installs the Python bridge dependencies, builds `desktop-rust` in release mode and launches it. Re-running it rebuilds incrementally and launches the current source. See [desktop-rust/README.md](desktop-rust/README.md) for requirements, layout and limitations.

The desktop client provides public YouTube Music search, artwork, a persistent queue with independent duplicate occurrences, synchronized LRCLIB lyrics, a bounded automatic audio cache and Ember-owned playlist sync through the mobile backend. It uses the checkout's Python core only for catalog search, stream resolution, audio downloads and lyrics; no Google account is used.

## Layout

- `desktop-rust/` — GTK4/Rust desktop client, embedded stylesheet, music bridge and shared-library client.
- `music.py` — public YouTube Music search, playlist loading and yt-dlp stream resolution.
- `audio_cache.py` — private, file-backed audio cache and offline downloads.
- `disk_cache.py` — bounded artwork and playlist-metadata cache.
- `services.py` — private JSON storage helpers.
- `playlist_search.py` — local playlist filtering used by the mobile API.
- `mobile/` — SwiftUI iPhone app and the authenticated backend for search, prepared M4A audio, lyrics and shared playlists.
- `transfer_playlist.py` / [TRANSFER.md](TRANSFER.md) — optional local CSV helper for reviewing Spotify playlist matches. It is not part of either app.
- [EMBER_PLAYLIST_SYNC_PLAN.md](EMBER_PLAYLIST_SYNC_PLAN.md) — shared-playlist architecture and rollout status.

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

The Rust desktop client does not implement radio, Last.fm scrobbling, MPRIS or Google account sync. Shared playlists are stored by the self-hosted backend, not by a provider account. The iPhone app has its own cache and playback limitations documented in [mobile/README.md](mobile/README.md).
