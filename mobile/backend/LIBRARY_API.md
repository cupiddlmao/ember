# Ember-owned playlist library — backend 0.3.0

Implemented locally; not deployed. SwiftUI integration is implemented in source (macOS build/device checks pending); Rust integration is implemented locally (native interaction checks pending).
This is one private library shared by devices holding the existing Ember bearer key.
There are no user accounts, provider logins, provider writes or network calls in the
library store. Existing `/v1/playlists/...` public-provider preview routes are unchanged.
This stores track references, not audio, playback queue or position. Playback availability
still depends on the recording/provider.

## Routes

All routes require `Authorization: Bearer <existing Ember key>`, including reads.
Both `/v1/library/...` and `/ember/v1/library/...` work. JSON replies are `no-store`.
Writes require `Content-Type: application/json` and a single `Content-Length` (no
chunked transfer). Maximum body is 8 MiB and reading it has a 10-second deadline.
Native clients should send a known-length JSON body. Existing request-rate limits apply.

| Method and path | Result |
| --- | --- |
| GET `/v1/library/playlists` | `{ "playlists": [summary, ...] }`, newest update first |
| POST `/v1/library/playlists` | Create using client-chosen UUID; HTTP 201 receipt |
| GET `/v1/library/playlists/{id}` | Summary plus ordered `entries` |
| PUT `/v1/library/playlists/{id}` | Atomic replacement of name and ordered entries; HTTP 200 receipt |
| DELETE `/v1/library/playlists/{id}` | Delete with JSON revision/request body; HTTP 200 receipt |

Summary fields: `id`, `name`, `revision`, `createdAt`, `updatedAt`, `trackCount`.
Timestamps are UTC ISO 8601. IDs are canonical lowercase hyphenated UUID strings.
Unknown fields are rejected rather than accidentally storing credentials or stream URLs.

Create body (generate fresh real UUIDs, not these example IDs):

```json
{
  "id": "f992c077-99de-48b9-94a3-ec00458fbfe3",
  "requestId": "c267cd61-8024-4120-9f55-11be9138e6be",
  "name": "GG",
  "entries": [
    {
      "id": "9e12c19b-bb12-4aa3-b9be-9fbd50c70fba",
      "track": {
        "source": "youtube",
        "id": "dQw4w9WgXcQ",
        "title": "Recording title",
        "artist": "Artist",
        "album": "Album",
        "durationMs": 213000,
        "artworkUrl": null
      }
    }
  ]
}
```

`entries: []` creates an empty playlist. Each occurrence has its own UUID, so two
copies of the same song are separate entries. Keep occurrence UUIDs when reordering.
Track fields match existing mobile search (`title`, `artist`, `durationMs`, etc.);
`source` defaults to `youtube`, album/duration/artwork may be omitted. Rust clients
must map `name`/`artists`/`duration_ms`/`artwork_url` to these API fields. Track IDs are
11-character YouTube IDs, duration 0–86400000ms. Artwork is null or bounded HTTPS on
the canonical YouTube thumbnail allowlist. No URI or temporary audio URL is stored.
Names are 1–120 characters; title/artist/album max 300 each. At most 200 live
playlists and 5000 entries per playlist. All deleted IDs are retained as tombstones;
at most 10000 total playlist IDs can be allocated. This prevents deleted IDs being
recreated by a delayed create retry. Track metadata is supplied by clients, not
verified against the provider; clients must use actual search results and render as text.

Edit body: same `name` and full `entries` array, replace create `id` with
`expectedRevision` and use a fresh `requestId`:

```json
{"requestId":"33a16edf-92b3-4c0e-aa02-3336a9b410d0","expectedRevision":1,"name":"GG renamed","entries":[]}
```

This example clears the playlist. To add/remove/reorder, GET the current snapshot,
make that change in memory, then PUT the complete resulting snapshot. Rename must
also preserve its current entries. The full snapshot approach keeps this first API
small and transactional; it is intended for a single person's bounded playlists.

Delete body:

```json
{"requestId":"7b475a4a-f74b-4adb-bd1f-9f3291a29296","expectedRevision":2}
```

Successful mutations return `{ "id": "...", "revision": 2, "deleted": false }`.
Create starts at revision 1; every edit/delete increments it. Receipts contain no
track snapshots; follow with GET (or list after delete) for the authoritative state.

## Conflicts and retries

- Revisions are checked inside SQLite `BEGIN IMMEDIATE` transactions. If another
  device saved first, return HTTP 409 `revision_conflict`, not a silent overwrite.
  Refresh and ask the user to reapply their edit. Never automatically overwrite with
  stale local contents.
- Generate a request UUID once per intended save; retry the exact same body and UUID
  after a timeout. A persistent ledger retains the latest 1000 successful mutations
  across the library, returning the original receipt/status even after later edits.
  GET after a replay; its receipt revision may be older than the current playlist.
- Reusing a retained request UUID with different contents returns 409 `request_conflict`.
  Beyond the receipt window, old updates conflict or deleted playlists return 404;
  old creates cannot reuse their playlist ID. Refresh before retrying old operations.
- 400 means invalid metadata/body; 401 invalid/missing Ember key; 404 missing/deleted
  playlist; 409 also covers quota/conflict; 413 oversized body; 415 wrong content type;
  429 rate limit; 503 storage busy/unavailable. Error envelope is the existing
  `{ "error": { "code": "...", "message": "..." } }`.

## Storage, backup and deployment

SQLite uses foreign keys and WAL, with per-operation connections and private file/
directory permissions. `EMBER_LIBRARY_DIR` defaults to `/var/lib/ember/library`.
Keep it OUTSIDE `EMBER_CACHE_DIR`; evicting audio must never erase playlists.
Compose mounts a separate `playlist-library` volume. The Docker image prepares the
mount directory for UID 10001. Restart/rebuild preserves data. `down -v` deletes it.

Keep project `ember-mobile`, the existing `.env` bearer key, audio-cache volume and
Tailscale `/ember` proxy. Compose 0.3.0 defaults to the user's loopback host port18080;
`EMBER_HOST_PORT` can preserve another existing host mapping. Container port stays8080.
No deployment or token rotation is required to develop/test this locally.

For a live consistent backup, use SQLite's backup API rather than copying only the
main DB while WAL is active. Example from `mobile/backend` on the server:

```bash
docker compose -p ember-mobile exec -T api python - <<'PYBACKUP'
from pathlib import Path
from mobile.backend.library_store import LibraryStore
LibraryStore(Path('/var/lib/ember/library')).backup(Path('/var/lib/ember/library/backup-before-upgrade.sqlite3'))
PYBACKUP
```

This refuses to overwrite an existing backup; use a fresh dated filename. Copy the
backup off the volume too (a same-volume copy cannot survive volume deletion).
To restore, stop the API, preserve the current DB/WAL/SHM as a rollback copy, restore
the backup as `playlists.sqlite3` in the library volume with UID10001 ownership and
0600 permissions, remove old WAL/SHM from the restored location, then start the API.
Never restore over a running database. Current schema version is1. Unknown versions
or unrecognized existing databases stop startup without migrating them. No upgrades
are implemented yet; any future migration must first make a consistent backup.

## Client integration status

- Rust now has server/key settings (private owner-only file), native library management and local desktop audio resolution. Use the same existing server/key as the phone.
- SwiftUI list/detail/create/rename/delete/reorder and Add to playlist are implemented in source using this API. Build/device checks remain pending. Rust library integration is also implemented locally; installation and deployed-device validation remain pending.
- Map canonical track metadata and keep separate occurrence IDs. Treat provider
  bookmarks as a separate feature; only an explicit import copies membership.
- Save success only after acknowledgment, refresh after mutations/foreground/library
  entry, and handle409 explicitly. No realtime service is necessary.
- Cache successful reads privately for read-only offline viewing. Do not allow offline
  edits or silently fork a local library in this first version.
- CSV matching/import is not implemented by these routes. A future importer can use
  create/PUT after matching and reviewing recordings.

## Checks

```bash
.venv/bin/python -m unittest mobile.backend.tests.test_library_store mobile.backend.tests.test_http_api
```

Tests use temporary directories and local ephemeral HTTP servers with fake music
services. No provider login, external network, real token or live library is used.
