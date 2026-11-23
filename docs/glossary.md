# Ember glossary

Terms as Ember actually uses them. Sources are the desktop client
(`desktop-rust/src/`), the Python core, and the shared library backend.

## Catalog and tracks

**Recording** — one canonical public YouTube Music track: id, name, artists,
album, duration, optional artwork URL. Ember never stores a provider account;
recordings are resolved to temporary public stream URLs when played.

**Resolution** — asking the Python bridge for a playable stream URL for a
recording. Resolution is not playback: a resolved stream can sit silent.

**Artwork** — an optional HTTPS thumbnail from an approved YouTube host. When
missing, the UI shows an **artwork fallback** (a letter tile or glyph), never a
stock image.

## Playback

**Player bar** — the persistent 48 px header region holding transport, the
now-playing surface (internally the "player pill", fixed 380 px wide), real
progress/seek, elapsed/duration, volume, Lyrics, Queue and the window close
control.

**Queue occurrence** — one independent entry in the queue, identified by a
stable queue-entry ID. The same recording may appear many times; each
occurrence is distinct and separately reorderable or removable.

**Pending queue** — a queue created by Play next / Add to queue while nothing
is playing. It stays silent until Play is pressed.

**Session** — the in-memory record of recordings found or selected during one
run of the app. Shown on Home as "This session"; never persisted.

**Song radio** — a finite related queue: Last.fm chooses recommendations on the
Ember backend, YouTube Music supplies matched recordings. The seed recording
plays first. There is no endless autoplay.

**Stop** — stops audio and cancels pending work but keeps the selected queue
occurrence so Play can retry it.

## Library

**Shared playlist** — an Ember-owned, backend-stored ordered list of track
references with a revision number, shared between desktop and iPhone. It is not
a provider playlist and requires no YouTube account.

**Library page** — the playlist detail destination: collage cover, name,
count/duration, Play, Shuffle, overflow menu, and the song rows.

**Revision conflict** — a save refused because the playlist changed on another
device since the client's snapshot. The client must refresh rather than
overwrite.

## Panels and modes

**Side panel** — the inset 340 px overlay on the right that hosts Queue or
Lyrics. Opening it never squeezes the content area.

**Immersive lyrics** — the borderless full-screen lyrics window (internally the
"fullscreen lyrics window") with artwork, metadata and transport. It shares the
same lyric data and playback position as the panel and never refetches.

**Following** — automatic scrolling to the active lyric line. Manual scrolling
suspends it until **Resume following**.

## Architecture terms that surface in the UI

**Bridge** — the bounded JSON subprocess (`desktop-rust/bridge.py`) that exposes
search, resolve, lyrics, cache and shared playlists to Rust. Temporary stream
URLs and credentials stay out of the UI.

**Lane** — one bridge worker with a single pending job (search, resolve,
lyrics, cache, library). Replacing a request cancels that lane's process and
ignores stale completions.

## Design vocabulary

**Craft pass** — a revision judged on type scale, surface hierarchy, spacing
rhythm, icon weight, state design, motion and copy, without changing the
product's information architecture.

**Surface ladder** — the five fixed elevation values (`canvas`, `sidebar`,
`raised`, `overlay`, `sunken`) from which every background must be chosen.

**Hero surface** — the large artwork-led header of a playlist or the immersive
lyrics window. The only place artwork-derived tint is allowed.

**Tint** — a dominant colour extracted from decoded cover pixels, clamped so
required text contrast survives, then crossfaded onto a hero surface.

**Derived section** — a Home section produced by grouping, ordering or
composing existing real data (album grouping, collage covers, recency), never
by generating content.

**Motion token** — one of the six named durations (90/120/180/220/320/420 ms)
sharing a single easing curve; every animation uses one and has an instant path
under `gtk-enable-animations=false`.

**State matrix** — the required set of sizes, data states, interaction states
and DPI scales that must be visually verified before the revision is done.
