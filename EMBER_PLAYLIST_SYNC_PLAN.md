# Ember-owned playlist sync

Status: backend SQLite/API is implemented locally in `mobile/backend/library_store.py` and documented in `mobile/backend/LIBRARY_API.md`. SwiftUI integration and private in-session offline read cache are implemented in source; Rust integration is also implemented locally; macOS build/device checks, native Rust interaction checks and deployment remain pending. The sections below describe the overall plan. Finish/review the Rust desktop UI first, then implement shared playlists, then redesign the iPhone SwiftUI using its own Apple Music phone reference.

## Recommendation
Keep the existing Python laptop backend and add SQLite using Python's standard library. The backend becomes the source of truth for Ember-owned playlists. Both Rust desktop and iPhone use authenticated HTTPS over the existing Tailscale /ember address. No YouTube account, playlist upload or provider playlist ownership is needed.

Local-only playlists are also possible, but would not automatically appear on the other device. For the requested PC/iPhone sharing, use backend-owned playlists with a read-only client cache when offline initially. Defer offline edits/conflict merging.

Playlist storage holds references and metadata, not audio files:
- playlist UUID, name, creation/update timestamps and revision
- ordered entries with unique entry UUID, source, YouTube track ID and canonical title/artist/album/duration/artwork metadata
- duplicate tracks remain independent occurrences
- no temporary stream URLs, cookies, access keys or audio bytes in the database
This syncs playlist membership/order, not the playing queue, playback position or downloaded audio.

## Minimal API
Use a distinct /v1/library/playlists namespace to avoid existing /v1/playlists provider-preview/page routes.
Support list/create, read/rename/delete, add/remove entries and reorder. Choose the smallest consistent route shape during implementation.
Validate bounded names, playlist/entry counts, canonical track IDs, request sizes and metadata. Require existing authentication on every route.
Mutations run in SQLite transactions and require the expected revision; reject stale edits with409 rather than silently overwriting changes from the other device. Return the new revision after success.
Make retrying a mutation safe using a request/entry UUID or another small idempotency mechanism; a network timeout must not duplicate an add.
Use SQLite foreign keys and stable IDs. Keep data in a dedicated persistent Docker volume outside the evictable audio cache. Back up the DB before migrations; do not regenerate the existing token or reset cache/Tailscale configuration.

## Client behavior
Online saves are immediately sent to backend; show success only after acknowledgement. Refresh on app foreground/library entry and after mutations; manual refresh and a small conditional poll while library is visible are enough initially. No realtime platform needed.
Cache last successful reads privately for offline viewing. Mark stale/offline state clearly; disable edits while offline in the first version. Do not silently create a second local-only library.
Desktop keeps its existing local YouTube resolution/playback; playlist sync does not force desktop audio through the laptop backend. iPhone continues its existing audio path.
Existing saved provider playlists stay intact. Explicit import copies their current playable canonical tracks into a new Ember playlist; it does not delete or modify the YouTube original. iPhone SavedPlaylists.json currently stores bookmarks, not editable track membership.
Account-backed/private playlist import, collaborative/multi-user sharing and provider writeback are later features.

## Rollout and checks
Backend schema/API first, then Rust client, then iPhone integration/redesign.
Test persistent restart, duplicate tracks, reorder/remove integrity, revision conflicts across two clients, retry idempotency, unauthorized mutations and offline read-only behavior.
Deployment must preserve live bind127.0.0.1:18080, Compose project ember-mobile, API token, existing audio volume and /ember proxy.
Backend schema/API now exist. SwiftUI integration is implemented in source but not yet built/device-tested. Rust sync is now implemented locally. CSV import UI, release installation and deployment remain pending.
