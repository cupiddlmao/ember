# Ember Mobile — native client and private backend

Backend 0.3.0 adds [Ember-owned shared playlists](backend/LIBRARY_API.md).
SwiftUI shared-library integration is implemented in source; the macOS build and
iPhone checks remain pending. Desktop integration is implemented in `desktop-rust/`.
Existing provider playlist bookmarks stay unchanged. No provider account is needed
for the new API.

A native SwiftUI iPhone client plus a small private backend. The app targets an iPhone 13 on iOS 26.6.2 and is built as an **unsigned device IPA** by a manual GitHub Actions workflow, then signed locally by SideStore.

This milestone adds a saved playlist library, ordered queue playback, and synced lyrics to the existing search and background-audio client. It keeps the dark/pink Ember interface and does **not** add a Google login, account sync, or a fake sign-in screen.

> `AGENTS.md` is not present in this repository. The desktop client lives in `desktop-rust/`; this document covers `mobile/`, the iOS workflow and the mobile backend.

## What is implemented

### iPhone app (`mobile/ios`)

- Dark/pink native SwiftUI interface with an Ember app icon.
- Server connection screen: HTTPS URL plus access key, with clear connection errors. A public base path such as `/ember` is preserved.
- Access key stored in the iOS Keychain (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`); never in `UserDefaults`, source, URLs, or logs.
- Debounced public song search with artwork, artist, album, and duration.
- Saved public/unlisted YouTube Music playlists:
  - Paste a playlist URL and preview its title, owner, artwork, track count, unavailable count, and first tracks.
  - Save the playlist metadata on the phone and reopen it from the Library tab.
  - Load every page before Play All, Shuffle, or a start-at-track action becomes available, so a large queue is never silently truncated.
  - Search the loaded tracks locally; the backend also supports a search query for API consumers.
  - Start playback at any track, play all in order, or shuffle the full playlist.
  - A playlist over the 5,000-track safety limit fails visibly instead of returning a partial list.
- Queue playback:
  - Tap a track to start it; the rest of the loaded playlist remains in queue order.
  - **Play Next** and **Add to Queue** are available from search results and playlist rows.
  - Automatic advance at track end, plus app and lock-screen next/previous commands.
  - Duplicate songs are distinct queue occurrences, identified by a UUID rather than by the YouTube video ID.
  - A queue sheet shows the current item and upcoming items. Upcoming items can be removed, moved up/down, or cleared; the current item cannot be removed or reordered.
  - Playback failures show a visible error and do not enter a retry loop. The user can skip or choose another item.
  - The next two queue items are prefetched; the active track and both prefetched items are protected from cache eviction. Stale downloads are cancelled/ignored by a queue generation and queue-entry guard.
  - Queue state is in memory and is reset when the app process is recreated. The library only persists metadata.
- Synced lyrics:
  - Opens from Now Playing; lines highlight from local player time.
  - Timed lines are buttons; tapping one seeks to that line.
  - Per-track sync offset: **Earlier** and **Later** shift lyric presentation in 0.25-second steps within ±5 seconds. Positive values make lyrics appear later; negative values make them appear earlier. The offset is saved locally by track ID, and **Reset** returns to 0. It only changes highlighting, scrolling, and tap-to-seek targets; adjusting it never seeks, changes speed, or moves the audio. The controls appear only for synced lyrics.
  - Manual scrolling pauses auto-follow and shows a **Resume** control. Dragging during playback does not seek.
  - Large, high-contrast lyric presentation with an artwork-derived background.
  - Honest states for line-synced lyrics, plain lyrics, instrumentals, no match, loading, and provider errors.
  - Word highlighting is used only when the provider explicitly supplies word-level timestamps. If a word has no end time, the next supplied word start is used as its visual boundary; no end time is invented. LRC word tags are not converted into guessed word timings.
- Existing playback behavior retained: local M4A download/playback, 512 MiB LRU cache, `.playback` audio session, interruption/headphone handling, background audio, lock-screen metadata/artwork, play/pause, seeking, skip, and ±15 seconds.
- No Google sign-in, cookies, account tokens, private playlists, or YouTube stream URLs in the app.

### Backend (`mobile/backend`)

- Small standard-library HTTP server; no web framework.
- Bearer-token authentication compared with `hmac.compare_digest`.
- Existing `GET /v1/health`, `GET /v1/search`, and `GET /v1/tracks/{id}/audio` routes.
- New authenticated routes:
  - `GET /v1/playlists/preview?url=…&limit=…`
  - `GET /v1/playlists/{id}?offset=…&limit=…&q=…`
  - `GET /v1/tracks/{id}/lyrics?title=…&artist=…&album=…&durationMs=…`
- Public and unlisted YouTube Music playlists only. Playlist links are parsed with the existing `music.music_link` helper and loaded through the existing anonymous `music.search` path. No Google account is involved.
- Playlist results preserve duplicate song occurrences and track order, skip malformed/unavailable entries, report an unavailable count, and cap a loaded playlist at 5,000 tracks. A larger playlist returns an explicit error.
- Playlist pages are bounded to 100 tracks and cached for 15 minutes; repeated page requests reuse the same parsed playlist.
- Lyrics use [LRCLIB](https://lrclib.net/) only. No API key is required, and the backend identifies itself with `EmberMobile/0.2.0 (https://github.com/cupiddlmao/ember)`.
- LRC line timing is supported. Word timing is accepted only from an explicit LRCLIB Lyricsfile YAML `words` array with start timestamps and optional end timestamps. Missing word ends stay missing; they are never estimated from line timing.
- PyYAML parses Lyricsfile with aliases disabled and a fixed, bounded schema.
- Fixed, credential-free error messages; unexpected exception details are never returned to the client.
- Request logs contain method, path, status, and duration only — no query strings, headers, or access keys.
- `GET /v1/...` and `GET /ember/v1/...` are both accepted, so the app works whether Tailscale Serve preserves or strips the `/ember` prefix.
- Docker image with Python 3.12, PyYAML, FFmpeg, and Node.js 22; Compose binds only to `127.0.0.1`.

## How playback and lyrics work

```text
iPhone app
  └─ HTTPS + Authorization: Bearer <key>
       └─ Ember backend on the Ubuntu VM
            ├─ ytmusicapi public search / public playlist load
            ├─ yt-dlp public audio download
            ├─ FFmpeg → AAC / M4A (44.1 kHz stereo, 128 kbps)
            ├─ bounded audio cache
            └─ LRCLIB public lyrics lookup (no key)
  ├─ authenticated GET …/audio → local .m4a → AVPlayer
  ├─ authenticated GET …/playlists/… → playlist pages → local library
  └─ authenticated GET …/lyrics → line/word timing → LyricsView
```

The prepared M4A path avoids relying on temporary YouTube WebM/Opus URLs, which are not a safe assumption for `AVPlayer`. The app receives stable track IDs, metadata, artwork URLs, and server-prepared audio. Lyrics metadata (title, artist, album, duration) is sent from the backend to LRCLIB; no access key or audio is sent there.

## Requirements

- Ubuntu VM with Docker and Docker Compose, **or** Python 3.10+ with FFmpeg and Node.js 20+ for local development.
- Tailscale on the VM and iPhone, with MagicDNS and HTTPS certificates enabled for the tailnet.
- Xcode is **not** required on your machine. The IPA is built by GitHub Actions.
- SideStore installed on the iPhone.
- No LRCLIB account or API key.
- No Spicy Lyrics key is configured or required. The app does not include a Spicy integration.

## Backend setup

### Option A: Docker on the Ubuntu VM (recommended)

From the repository root:

```bash
cd mobile/backend
# Keep an existing .env. On a fresh checkout only:
[ -f .env ] || cp .env.example .env
chmod 600 .env
# If .env is new, put a random token in EMBER_API_TOKEN:
openssl rand -hex 32

docker compose -p ember-mobile up -d --build
docker compose -p ember-mobile ps
```

The image tag in Compose is `ember-mobile-api:0.3.0`. The build installs `PyYAML`, copies `playlist_search.py`, and includes the new playlist/lyrics services. The named audio-cache volume is preserved, and a separate playlist-library volume stores SQLite data outside the audio cache. Keep Compose project `ember-mobile`, the existing `.env` token and Tailscale `/ember` mount. Compose defaults to `127.0.0.1:18080` (set `EMBER_HOST_PORT` to preserve a different existing mapping). Do not run `down -v`. See [library storage and backups](backend/LIBRARY_API.md).

Check health without putting the key in a URL:

```bash
set -a; . ./.env; set +a
curl -fsS -H "Authorization: Bearer $EMBER_API_TOKEN" \
  http://127.0.0.1:18080/v1/health
# {"status":"ok","version":"0.3.0"}
```

Preview a public playlist:

```bash
curl -fsS -G -H "Authorization: Bearer $EMBER_API_TOKEN" \
  --data-urlencode "url=https://music.youtube.com/playlist?list=PLAYLIST_ID" \
  --data-urlencode "limit=50" \
  http://127.0.0.1:18080/v1/playlists/preview
```

Load another page:

```bash
curl -fsS -G -H "Authorization: Bearer $EMBER_API_TOKEN" \
  --data-urlencode "offset=50" \
  --data-urlencode "limit=100" \
  http://127.0.0.1:18080/v1/playlists/PLAYLIST_ID
```

Look up lyrics for a public track:

```bash
curl -fsS -G -H "Authorization: Bearer $EMBER_API_TOKEN" \
  --data-urlencode "title=Track title" \
  --data-urlencode "artist=Artist name" \
  --data-urlencode "album=Album name" \
  --data-urlencode "durationMs=233000" \
  http://127.0.0.1:18080/v1/tracks/VIDEO_ID/lyrics
```

Never add the access key to a query string: the server only reads the `Authorization` header.

### Option B: Local Python process

From the repository root:

```bash
.venv/bin/pip install -r requirements.txt -r mobile/backend/requirements.txt
cd mobile/backend
[ -f .env ] || cp .env.example .env
chmod 600 .env
# Put a random token in .env if it is new
cd ../..
set -a; . mobile/backend/.env; set +a
.venv/bin/python -m mobile.backend
```

The process requires `ffmpeg` and `node` on `PATH` and exits with a clear message if either is missing. The default bind address is `127.0.0.1`; keep it that way and let Tailscale Serve terminate HTTPS.

### Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `EMBER_API_TOKEN` | required | Bearer key; at least 24 characters. Use `openssl rand -hex 32`. |
| `EMBER_BIND` | `127.0.0.1` | Bind address. The Docker image sets `0.0.0.0` inside the container, while Compose publishes only to loopback. |
| `EMBER_PORT` | `8080` | HTTP port. |
| `EMBER_CACHE_DIR` | `/var/lib/ember/cache` | Converted M4A cache. |
| `EMBER_CACHE_MAX_MB` | `2048` | Backend audio cache budget. |
| `EMBER_MAX_AUDIO_MB` | `48` | Maximum size of one prepared file. |
| `EMBER_MAX_JOBS` | `3` | Concurrent search/audio/playlist/lyrics jobs. |
| `EMBER_RATE_LIMIT` | `60` | Requests per client per minute. Tailscale Serve makes requests appear to come from loopback, so in practice this is one shared budget. |

`mobile/backend/.env` is ignored by Git. `.env.example` contains only a placeholder. The iOS app has a separate 512 MiB audio cache; the backend cache is not the phone's cache.

### API

All routes require `Authorization: Bearer <EMBER_API_TOKEN>`. Errors are JSON:

```json
{"error":{"code":"unauthorized","message":"A valid access key is required."}}
```

| Route | Result |
| --- | --- |
| `GET /v1/health` | `{"status":"ok","version":"0.3.0"}` |
| `GET /v1/search?q=…&limit=20` | `{"tracks":[…]}`; query max 200 chars, limit clamped to 1–25 |
| `GET /v1/tracks/{11-char-id}/audio` | `audio/mp4` M4A bytes |
| `GET /v1/playlists/preview?url=…&limit=50` | Playlist summary plus the first bounded page |
| `GET /v1/playlists/{id}?offset=0&limit=50&q=` | One page, duplicate-preserving track order, `total`, and `hasMore` |
| `GET /v1/tracks/{11-char-id}/lyrics?title=…&artist=…&album=…&durationMs=…` | `kind` (`synced`, `plain`, `instrumental`, or `none`) and lyric lines |

Track objects contain `id`, `title`, `artist`, `album`, `durationMs`, and `artworkUrl`. Playlist summaries contain `id`, `title`, `owner`, `artworkUrl`, `trackCount`, and `unavailableCount`. Upstream URLs are never returned.

Playlist limits:

- Public and unlisted playlists only. Private playlists are rejected with a safe, visible error.
- Maximum 5,000 available tracks per playlist; larger playlists fail rather than silently truncating.
- Page size is clamped to 1–100. The app uses 100 and loads all pages before enabling playback of the full list.
- Unavailable or malformed entries are skipped and counted in `unavailableCount`.

## Private HTTPS with Tailscale

Keep the existing `/ember` proxy if already configured. For a new mount with Docker listening on `127.0.0.1:18080`:

```bash
tailscale serve --bg --https=443 --set-path=/ember http://127.0.0.1:18080
tailscale serve status
```

Serve gives you a private HTTPS origin such as:

```text
https://ember-vm.tailnet-name.ts.net
```

If the existing deployment exposes the API under a base path, use that path in the app, for example:

```text
https://homelab-dashboard.tail677874.ts.net/ember
```

Enter the server origin only — **without** a trailing `/v1`. The app builds `/v1/...` requests from it. The backend also accepts `/ember/v1/...`, so a Serve mount that preserves `/ember` works without another proxy rewrite. Open the HTTPS origin in the iPhone's browser first to confirm the certificate and Tailscale path. A `404` at `/` is expected because the API lives under `/v1`; a trusted HTTPS response is the important part.

Do **not** use `tailscale funnel`: this is intentionally tailnet-only, not a public deployment. Do not publish port 8080 on the VM's public interface.

## Build the unsigned IPA

The workflow is `.github/workflows/ios-unsigned.yml`. It is **manual only** (`workflow_dispatch`), runs on `macos-26`, and uses no Apple account, certificate, provisioning profile, or signing secret.

1. Commit and push the workflow and `mobile/ios` when you are ready. (This change was not committed or pushed here.)
2. In GitHub, open **Actions → iOS unsigned IPA → Run workflow**, choose the branch, and start it.
3. Wait for the build. On success, download the **EmberMobile-unsigned-ipa** artifact; it contains `EmberMobile-unsigned.ipa`.
4. Unzip the artifact if your browser downloaded it as a `.zip`.

Before the device build, the workflow runs the audio-cache, queue, app-model, and lyric-timing self-checks. `QueueCheck` covers queue ordering, duplicate entries, start-at-track, remove/reorder/clear, previous/end behavior, and stale generation rejection. `LyricsTimingCheck` covers offset direction, the ±5-second bounds, and the reversed tap-to-seek conversion.

The workflow builds with:

```bash
xcodebuild -project EmberMobile.xcodeproj \
  -scheme EmberMobile \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO \
  CODE_SIGNING_REQUIRED=NO \
  CODE_SIGN_IDENTITY="" \
  EMBER_GIT_COMMIT="$(git rev-parse --short=7 HEAD)" \
  build
```

It then packages `Payload/EmberMobile.app` into an IPA, verifies there is no `_CodeSignature`, checks that `UIBackgroundModes` contains `audio`, verifies that `EmberGitCommit` matches the checked-out short SHA, prints a SHA-256, and uploads the artifact. If the build fails, the **xcodebuild-log** artifact is uploaded for diagnosis.

The app bundle identifier is `com.ember.mobile`; version `0.2.0` (build `2`). SideStore re-signs the app, so CI never needs your Apple ID.

The in-app **Settings** screen shows the bundle's version and build number, plus the short Git commit injected by the workflow. Local Xcode builds use the `local` fallback and show **Local build**.

## Install with SideStore

1. Transfer the downloaded `EmberMobile-unsigned.ipa` to the iPhone (AirDrop, Files/iCloud Drive, or a local transfer method).
2. Open SideStore → **My Apps** → **+** (or the install button) and choose the IPA.
3. SideStore signs it with the Apple ID already configured in SideStore. Follow its prompts.
4. Launch **Ember**, enter the Tailscale HTTPS origin (including `/ember` if that is the public base path) and the same `EMBER_API_TOKEN` value from the server `.env`, and tap **Connect**.
5. Open **Library**, paste a public/unlisted playlist URL, preview it, and save it. Load the playlist completely before using Play All, Shuffle, or start-at-track.

A free Apple ID installation expires after 7 days; refresh it in SideStore. The workflow itself requires no Apple account. It also never reads or modifies your pairing files or existing desktop credentials.

## Queue and lyrics limitations

- Queue state is not persisted across app launches. Saved playlists are; their track lists are re-fetched when opened.
- The current queue item has no remove action; clear/remove/reorder apply to upcoming items only.
- A failed download does not auto-retry or auto-skip. This avoids retry loops; use next/previous or select another track.
- The mini-player and queue stay accessible after a failed download. Play retries the selected queue entry; Next skips it. Seeking is disabled until that selection is loaded.
- Pause, headphone removal, or an audio interruption during preparation cancels the pending start. The selected entry stays queued; tap Play to retry. A canceled download cannot start playback later.
- Only the next two items are prefetched. iOS can suspend network work after a period in the background, so a long queue may eventually stop at a missing file when the phone is locked. Keeping Ember open lets prefetch continue. The queue does not claim gapless or guaranteed lock-screen continuation for an unlimited queue.
- iOS can purge cached audio files. A re-download is attempted when the app is active; a failure is shown rather than hidden.
- Lyrics come from LRCLIB, not Apple Music. LRCLIB may have no match, plain text only, or line sync without word timing. Instrumental and missing states are intentional, not errors.
- Word timing is rare and is shown only when LRCLIB's Lyricsfile contains explicit word timestamps. Spicy Lyrics is not integrated: the existing Spicy helper is keyed by a Spotify recording ID, while these mobile tracks use YouTube IDs. No Spicy key is needed or stored.
- No Google sign-in, private playlist access, account sync, or server-side library persistence.

## Verification

### Tested here

- **Root test discovery (shared core, desktop bridge and mobile backend) passes.**
- **Backend suite passes**, including new playlist parsing/pagination, duplicate preservation, unavailable-count handling, the 5,000-track limit, LRCLIB 404/plain/instrumental/synced responses, explicit word timings, alias rejection, HTTP auth, and `/ember` prefix routing. The existing audio cache, FFmpeg, auth, rate-limit, and error-safety tests remain in the suite.
- **Live public playlist path**: the local Python service loaded a real public Tycho playlist with **48 tracks** through `PlaylistService.preview`, returned the first 2 tracks, and reported zero unavailable tracks.
- **Live LRCLIB path**: an exact lookup for “I Want to Live” by Borislav Slavov returned **40 synced lines**; LRCLIB supplied no word timings for that record, and the backend did not invent any. No key was used.
- **Live word-timed Lyricsfile**: an exact lookup for “Still Alive” returned **47 lines and 278 explicit word starts** through the real Lyricsfile YAML. The words had no `end_ms` values, so the backend preserved the missing ends rather than estimating them.
- **Docker image rebuilt successfully** as `ember-mobile-api:0.2.0`. A running container returned `{"status":"ok","version":"0.2.0"}` with the bearer key, `401` without it, logged only the request path/status, and included **PyYAML 6.0.3**.
- **Static iOS checks**: `Info.plist` parsed with the `audio` background mode; scheme/workspace XML parsed; workflow YAML parsed; all 21 app Swift sources are registered in the Xcode project; project object references are internally consistent. The latest timing/build files were checked structurally but not compiled or parsed by a Swift tool.
- No credentials were written to source, logs, URLs, or this README.

### Not verified

- **No Xcode build or IPA was produced here.** This machine is Linux with no Xcode/macOS, and the GitHub Actions workflow has not been run. The Swift app has not been compiled or type-checked locally.
- **`QueueCheck.swift`, `AudioCacheCheck.swift`, and `LyricsTimingCheck.swift` were not run locally** because Swift is unavailable. They are wired into the manual Mac workflow, but their runtime result is unverified.
- **No iPhone installation, launch, UI walkthrough, queued playback, background playback, lock-screen next/previous, prefetch-while-locked, or lyrics interaction was tested.** The new sync-offset controls and Settings build card were not viewed on a device or simulator.
- **The GitHub build-identification path was not exercised.** The workflow injects `EMBER_GIT_COMMIT` and verifies it in the packaged `Info.plist`, but the unsigned build has not run here.
- **Word-synced lyrics were not rendered on an iPhone.** The live LRCLIB response was parsed successfully, but the SwiftUI word-highlight/scroll behavior and the start-only-word fallback were not viewed on a device; that record also had no word `end_ms` values.
- **No Tailscale Serve, `/ember` phone connection, SideStore signing, or certificate check was tested.**
- **The Docker container was not exercised against live YouTube**; the live playlist/LRCLIB checks used the local Python environment. The container smoke test covered startup, auth, version, PyYAML, and logging.
- The earlier milestone's live audio download/playback result was not rerun in this pass. The existing backend audio tests still pass.

Do not treat static Xcode checks or backend tests as proof that the app compiles or that iPhone playback, queue continuation, or lyrics rendering work. The first real test must be a workflow build followed by a SideStore install.

## Deliberately deferred

Google account login, private playlist access, account/library sync, offline playlist downloads, persistent queue state, Spicy Lyrics, Apple Music lyrics, radio, Last.fm, public hosting, and any fake login screen. Search remains public-catalog only, and playlists remain public/unlisted only.

## File map

```text
mobile/
  backend/
    __main__.py              python -m mobile.backend entry point
    config.py                environment settings and validation
    http_api.py              authenticated HTTP routes and limits
    music_service.py         desktop music.py reuse + AAC conversion
    audio_cache.py           bounded LRU M4A cache
    playlist_service.py      public playlist preview/pages, cache, limits
    lyrics_service.py        LRCLIB lookup, LRC/Lyricsfile parsing
    requirements.txt         includes PyYAML
    tests/                   backend tests
    Dockerfile
    docker-compose.yml
    .env.example
  ios/
    EmberMobile.xcodeproj/   hand-written Xcode project + shared scheme
    EmberMobile/
      EmberMobileApp.swift
      Theme.swift
      Models/Track.swift
      Models/PlaybackQueue.swift
      Services/KeychainStore.swift
      Services/APIClient.swift
      Services/PlaylistStore.swift
      Services/PlayerController.swift
      Services/AppModel.swift
      Services/LyricsTiming.swift
      Views/… (Search, Library, Playlist, Queue, Lyrics, Now Playing)
      Assets.xcassets/
      Info.plist
    tests/
      AudioCacheCheck.swift
      QueueCheck.swift
      LyricsTimingCheck.swift
.github/workflows/ios-unsigned.yml
```

## Backend tests

```bash
.venv/bin/python -m unittest discover -s mobile/backend/tests -t . -v
```

The tests do not contact YouTube Music or LRCLIB and do not need a token. The real transcoding test is skipped if `ffmpeg`/`ffprobe` are unavailable.

## 2026-11-05 milestone notes

- Added the mobile library/queue/lyrics surface without changing the desktop app.
- Backend version and Compose image tag are `0.3.0`; the iPhone bundle version is independent. Shared-library client integration is implemented in the Rust client and SwiftUI source.
- The queue self-check runs before the unsigned IPA build. It checks ordering, duplicate IDs, cancellation generations, and end-of-queue behavior.
- After installing on iPhone, verify in particular: full playlist load and start-at-track; Play Next/Add to Queue with a duplicate track; remove/reorder/clear while playing; next/previous from the lock screen; locking the phone during a long queue; lyric tap-to-seek; manual scroll/Resume; and plain/instrumental/no-lyrics states with real tracks.

## Review fixes — 2026-11-05

- Fixed the prefetch protected-ID array/set type mismatch.
- Play All and Shuffle require a fully successful playlist load. A failed later page leaves both disabled; an empty nonfinal page or incomplete final total is rejected.
- Player controls use the selected queue entry even after a download fails, preserving retry, next/previous and queue access.
- Pause requests cancel preparation and invalidate stale completions; resume retries the selection instead of playing an old AVPlayer item. Late end-of-track callbacks cannot advance a selection that has not started.
- Added AppModelCheck.swift to the manual Mac workflow. It compiles the real AppModel against deterministic network/audio doubles and checks pause during a cancellation-ignoring download, resume/retry, a stale end notification, and failed-download queue recovery. It does not test AVFoundation, SwiftUI rendering, or actual network requests.

Local verification for these fixes: workflow YAML parses and the four AppModel-check source paths exist; git diff --check passes. Swift/Xcode are unavailable on this Linux machine, so the new Swift self-check and iPhone build have not run. Backend files were unchanged by these fixes; the review run previously passed 37 backend tests. No push, hosted build, backend restart or phone update was performed.

Device regression check after rebuilding: interrupt a multi-page playlist load and confirm Play All/Shuffle remain disabled; fail a song download and use retry/queue/Next; pause or disconnect headphones during preparation and confirm it stays silent until Play.


## iPhone shared playlists (0.3.0 source integration)

The Library tab now has an **Ember playlists** section and a separate **Saved YouTube
links** section. Existing local YouTube bookmarks are preserved. The same HTTPS
server address and Keychain access key are reused; no provider login is added.
Requires the laptop backend 0.3.0 routes. Older servers show an upgrade message.

- Create multiple Ember playlists; open one and Play All or tap an occurrence.
- Long-press a search result or provider-playlist song and choose **Add to playlist**,
  or use the plus button in Now Playing. Pick an existing playlist or create one
  with the song. Adding the same song again creates a separate occurrence.
- In an Ember playlist, use **Edit** for removal/reordering and the actions menu for
  rename/delete. Deletion asks for confirmation because it affects every device.
- Server acknowledgment is required before an edit is treated as saved. A conflicting
  revision is rejected with a reload message; edits never overwrite a newer revision.
- Ambiguous transport/5xx failures retain the exact request UUID/body privately on
  disk. **Retry pending save** reuses it, including after app relaunch. New edits are
  blocked until the pending request is resolved. Retrying never creates a new add ID.
- Successful reads are cached in protected Application Support files, excluded from
  backups and separated by a hash of the server URL. No access key/audio URL is stored
  there. In an established app session, a failed refresh shows cached data read-only.
  The existing launch connection flow still requires a reachable server to open the
  main app after a cold launch; full offline launch is not part of this integration.
- Refresh on library entry, foreground and pull-to-refresh; details refresh when opened.

This source update does not deploy the laptop API or install an IPA. Build the new
0.3.0 (build3) IPA with the existing macOS workflow and check it on the phone after
updating the backend. Verify duplicate songs, swipe delete/reorder, cross-device409
conflicts, airplane-mode cache/read-only state and retry after an interrupted save.
The new macOS `SharedLibraryCheck.swift` uses URLProtocol fixtures to check authenticated
routes, identical persisted retry payloads, duplicate IDs, conflicts and offline/server
cache isolation. It has not been executed on Linux; SwiftUI rendering and compilation
require Xcode/macOS.
