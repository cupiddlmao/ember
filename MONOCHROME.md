# Monochrome integration findings

Reviewed 2026-11-04. User has replaced SoundCloud direction with keeping Ember UI and investigating Monochrome streaming.

## Reusable architecture

- Keep Python/GTK UI, GStreamer Audio, MPRIS, queues and lyric renderer.
- Add a Python source adapter mapping Monochrome tracks to Ember fields: id, uri, name, artists, album.images, duration_ms, source.
- Upstream js/tracks-api.js uses https://tracks.monochrome.st/search/tracks?q=...&limit=20 for search and /track/{id} for stream URLs. Artwork and artist metadata are returned by search. Do not guess duration units without checking a live response.
- Upstream normalizes playlists and exposes album/artist metadata. Playlist import, discovery and recommendations require separate integration; do not claim these work after only search/playback.
- Keep existing LRCLIB/import/Spicy Lyrics integration; map lyrics by recording metadata, not foreign provider IDs.
- Preserve existing SoundCloud likes and account files; never relabel old track IDs as Monochrome IDs.

## Access and verification blocker

The README says its service accepts official Monochrome instances and localhost self-hosting, while custom hosted deployments need a working paid HiFi endpoint. Account sync is not available on self-hosted instances per README. Open-source frontend permission does not establish access to hosted services.

A direct unauthenticated GET to tracks.monochrome.st/search/tracks?q=Tycho%20Awake&limit=1 with Ember user agent and no forged origin timed out after 15 seconds. This does not prove an authorization denial or that the service is globally down. No live search response, stream, native decoding or backend integration has been verified.

Next: verify a supported reachable API route or obtain an endpoint intended for third-party clients. Then implement and test a single search-to-native-playback path before migrating the installed app. Avoid bypassing origin/auth/Turnstile restrictions.

## License and sources

Monochrome is Apache-2.0 (repository license file). Adapted code needs applicable license/copyright notices and modification notices; inspect any NOTICE file when copying code. The license does not grant rights to third-party music.

- https://github.com/monochrome-music/monochrome/blob/main/README.md
- https://github.com/monochrome-music/monochrome/blob/main/js/tracks-api.js
- https://github.com/monochrome-music/monochrome/blob/main/js/api.js
- https://github.com/monochrome-music/monochrome/blob/main/license

## Existing work state

Interrupted SoundCloud playlist bug fix remains in workspace soundcloud.py/test_soundcloud.py and was not installed. Its 17 unit tests passed, but live playlist extraction result was not collected before interruption; do not claim playlist fixed. No Monochrome implementation or installed-app change was made during this investigation.

## Local compatibility test — 2026-11-04

Cloned upstream into `/tmp/ember-monochrome-research`; clean `npm install --package-lock=false --no-audit --no-fund --prefer-offline` completed successfully (977 packages). Bun lock preserved. Initial interrupted install left truncated dependencies; a clean reinstall resolved local module errors. Started unmodified Vite config on `http://127.0.0.1:5176`, PID197210, log `/tmp/ember-monochrome-vite.log`; HTTP200 and browser-rendered Home verified. This is a temporary research checkout, not the installed Ember app.

Submitted Tycho Awake through the local app's Search: results did not populate. A bounded browser fetch to the track search endpoint timed out. Direct HTTPS checks to the API root and track search returned HTTP403; search response body was `error code: 1010` (Cloudflare rejection). The official website also produced CORS/network search failures in this session. These results establish access failure here, not a global outage or proof of the specific policy causing it.

No tracks were retrieved, so no stream resolution or native playback verification was possible. No production/installed Ember changes, backend adapter, account migration or deployment performed. Next step is supported API access/service recovery (or a separately provisioned compatible endpoint), then the existing search-to-GStreamer integration plan. Local frontend hosting alone does not resolve remote service rejection.
