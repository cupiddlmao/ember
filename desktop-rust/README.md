# Ember Rust desktop — native search, artwork, queue and lyrics

A separate, native Linux client for Ember using Rust, GTK4, GStreamer and
libsoup. It keeps the dark/pink Ember direction and covers a focused part of
the desktop migration:

- a compact 56–64 px persistent player across the top, with previous,
  play/pause, next, stop, artwork, a bounded now-playing surface, real
  progress/seek, elapsed/duration and volume
- a responsive 204 px sidebar with catalog search, the working Home, Search,
  Queue and Library destinations, and a scrollable shared-playlist section with
  text-only rows and a clear selected state
- a Home overview with square shared-playlist cards and the current session's
  unique recordings found or selected during this run
- public YouTube Music search with artwork result rows and queue menus
- a native queue with duplicate occurrences and stable queue-entry IDs
- artwork thumbnails loaded asynchronously with bounded network, memory and
  decode limits
- automatic LRCLIB lyrics with synchronized-line following and seek

This is the Ember desktop app. It uses the checkout's Python music core through
a bounded bridge for public YouTube Music search and resolution, and the Ember
backend for shared playlists. Radio and account sync are not implemented.

## Requirements

- Linux with a desktop session
- Rust **1.92+** and Cargo (developed with 1.99.1)
- GTK **4.12+** development/runtime packages (developed with 4.24.1)
- GStreamer **1.24+** development/runtime packages (developed with 1.28.8),
  including `playbin`, `uridecodebin`, `souphttpsrc`, `autoaudiosink`, and
  codecs needed by the selected YouTube stream
- libsoup **3** development/runtime packages (developed with 3.8.1)
- the checkout's Python environment. Run `bash run.sh` from the checkout; it
  creates `.venv`, installs `ytmusicapi`/`yt-dlp`, builds this app and launches
  it. yt-dlp may use Node.js for YouTube JavaScript processing.
- outbound access to YouTube, the approved YouTube artwork hosts and LRCLIB.
  No LRCLIB key or account is needed.

On Debian/Ubuntu-style systems the native packages are typically
`libgtk-4-dev`, `libgstreamer1.0-dev`, `gstreamer1.0-plugins-base`,
`gstreamer1.0-plugins-good`, `gstreamer1.0-plugins-soup`,
`libsoup-3.0-dev`, and a suitable codec package. Package names vary by
distribution.

## Build and run

```bash
cd desktop-rust
cargo build --release
./target/release/ember-rust
```

`cargo run --release` works as well. Dependencies are pinned by
`desktop-rust/Cargo.lock`.

The app discovers the checkout by walking ancestors of the executable (and the
source directory for development) until it finds `music.py`, `.venv/bin/python`
and `desktop-rust/bridge.py`. The launch working directory is not assumed. To
run a binary from another location:

```bash
EMBER_CHECKOUT_ROOT=/absolute/path/to/ember ./target/release/ember-rust
```

## Layout and interaction

The default window is 1180×760 and remains usable at its 780×580 minimum. The
204 px sidebar has compact brand/search at the top, a restrained Session group,
the Queue count and a scrollable Playlists section. Shared playlists appear with
small covers, names and counts; the selected row is clearly marked. A small **+**
beside the Playlists heading opens the New playlist dialog. There are no inactive
Browse, Radio, Artists, Albums or fake rows.

Home shows real shared-playlist cards followed by up to ten unique recordings
from successful searches and chosen recordings in the current run. Both grids
use square cards with captions, four columns at wide sizes and two at the
minimum width. Playlist cards show shared covers when known and a designed
letter fallback otherwise; nothing is invented and no recommendation or mock
catalog is generated. The session section keeps its compact search empty state.

The Library destination opens the selected playlist as a dedicated page: a large
square collage cover, name, song count and duration, primary Play and a compact
overflow menu for Rename/Delete. Song rows have artwork, title, artist, duration
and an overflow menu containing Play next, Add to queue, Move up, Move down and
Remove; there are no four permanent row actions. At the minimum width the header
stacks with the cover above the title and controls, and long names truncate with
a full tooltip. Empty playlists retain their header, disabled Play and a clear
empty message.

The compact top player contains the transport controls, a bounded now-playing
surface with current artwork and metadata, an integrated real seek bar,
elapsed/duration, volume, Lyrics and Queue. Stop remains a visible, accessible
control. The queue/lyrics panel is an inset 340 px overlay on the right rather
than a layout column, so opening it cannot squeeze the result list or Home grid.
It has a pinned close button. At the minimum size the overlay still fits within
the window.

Search result rows retain their artwork, readable/ellipsized metadata,
duration, queue menu and activation behavior. The Home cards activate the
corresponding real recording and replace the queue with the current recent
list, matching search-row activation semantics.

## Artwork

The bridge copies an optional artwork URL from existing track metadata. Both
`bridge.py` and the Rust model require HTTPS, no credentials or port, and one of
the canonical YouTube image hosts (`i.ytimg.com`, `img.youtube.com`,
`lh3`–`lh6.googleusercontent.com`, `yt3.ggpht.com`,
`yt3.googleusercontent.com`).

Transport uses the installed libsoup 3 stack asynchronously on the GTK main
loop. Redirects are disabled so a response cannot leave the approved hosts.
Each request has a 15-second timeout, at most two requests run at once, bodies
are read in 32 KiB chunks and are rejected above 2 MiB, and PNG/JPEG/WebP decoding runs off the UI thread with a 1024 px dimension
limit and a 16 MiB decoder allocation budget. At most two requests, including
decoding, run at once. Up to 64 textures are cached in memory. The pending
backlog is capped at 64 URLs and 64 targets per URL; oldest queued work is
dropped if full. Weak widget targets are deduplicated and obsolete targets
are pruned before scheduling, so removed rows are not kept alive. Placeholders remain available while loading or on failure, stale
deliveries are ignored after a row/panel/track changes, and closing the window
cancels active requests. No audio or image is written to disk.

## Queue behavior

Search result rows have a menu for **Play next** and **Add to queue**. The
latest Play next choice is inserted immediately after the current occurrence.
Add to queue appends. With no current entry, queue actions create a pending
queue without starting audio; pressing Play starts its first entry.

Activating a search result replaces the queue with the displayed search
results and starts that occurrence. The Queue panel shows the current
recording and upcoming entries. Upcoming entries can be selected, moved up or
down, removed, or cleared without interrupting the current stream. The current
occurrence is never removable through an upcoming action.

Previous restarts the current recording after three seconds; earlier in the
track it selects the previous occurrence, or restarts the current one at the
queue start. End of track advances once and stops cleanly at the queue end. Stop
preserves the selected queue entry for retry. A failed resolve or playback
keeps that entry and does not auto-skip.

If playback is paused while the bridge is preparing audio, autoplay is
cancelled. The resolved stream remains silent until Play is pressed
explicitly. A fresh GStreamer player and bus watch are created for each started
stream, so delayed state/EOS/error messages from an old pipeline cannot affect
a newer selection; only the playbin's own Playing state is treated as ready.
Child bus errors still reach the app with player-ID isolation.

## Lyrics

Selecting a different recording starts a separate, cancellable LRCLIB lookup
lane. It cannot block search or stream resolution. Explicit states are shown for
loading, synchronized lyrics, plain lyrics, instrumental tracks, missing lyrics
and provider errors; errors and missing results provide a Retry button.

Synchronized lines use only LRCLIB's real timestamps. The current line is
highlighted by color without changing text or row geometry. The panel follows
the GStreamer playback position, suspends following on manual scrolling, and
offers **Resume following**. Clicking a timed line seeks the active GStreamer
stream after clamping the target to the known duration and restores following.
Plain lyrics are not seekable. LRCLIB attribution is shown in the panel. Auto-follow is cancelled on
manual scrolling, track changes, panel changes and window close.

The side panel's fullscreen control opens a borderless immersive lyrics window
without another lyrics request or a playback restart. It reads the same
`LyricsData` and player position, repeats loading, plain, instrumental, missing
and error states, and adds restrained artwork, song metadata and playback
controls. **Escape** or the visible **Exit** control closes it; the main window's
size, panel and playback state are untouched. Manual scrolling pauses only the
fullscreen follow position and shows **Resume following**. Both presentations
use the same color-only active-line treatment so words never move when timing
advances, and honor the GTK reduce-animation setting for scrolling.

## Temporary Python bridge

The prototype does not reimplement YouTube extraction. It starts:

```text
<checkout>/.venv/bin/python <checkout>/desktop-rust/bridge.py --root <checkout>
```

with explicit arguments, no shell interpolation and the checkout as the child
working directory. A single bounded JSON request is written to stdin and one
bounded response is read from stdout. The Rust reader caps output at 10 MiB to accommodate shared playlists;
`bridge.py` caps music JSON at 512 KiB and library JSON at 10 MiB. Searches are limited to 200
characters and 20 tracks. Interactive requests time out after 35 seconds;
background cache jobs have a separate 180-second deadline. Stderr is
discarded so raw subprocess diagnostics and credentials cannot reach the UI or
logs. Errors are structured and sanitized before display.

The bridge imports the checkout's existing `music.search()` and
`music.resolve()`. It copies a validated optional artwork URL through search
and resolve so the row, Home card and player thumbnail agree. Lyrics use the
existing `mobile.backend.lyrics_service.LyricsService` LRCLIB implementation
and are bounded again by the bridge: at most 800 lines, 500 characters per
line, and integer millisecond timestamps within 24 hours. It does not read or
migrate login/account configuration. Temporary stream URLs are kept in memory
only, passed to GStreamer's `playbin`, and redacted from debug output. They are
never displayed or stored.

Search, resolve, lyrics, caching and shared playlists each have their own worker and one pending job.
Replacing a request cancels that lane's active Python process group and ignores
its stale completion. Closing the window shuts down all five lanes, cancels
artwork fetches and stops GStreamer.

## Checks

```bash
cd desktop-rust
cargo test --locked
python3 -m unittest -v test_bridge.py
```

The focused tests cover queue occurrence IDs, Play next ordering, upcoming-only
edits, stale resolve rejection, paused preparation, stop/retry, single
end-of-track advancement, lyric states, timestamp validation, bridge response
bounds, sanitization, timeouts, process termination, artwork host validation,
seek conversion and bounded artwork response bodies. Run the commands above; they report the current Rust and Python test counts.

The native shell was inspected in an isolated X11 session at 1180×760 and
780×580, including populated Home at both sizes, shared-playlist cards, a real
four-cover playlist page, reflowed narrow header, aligned song rows, long-name
truncation, the empty-playlist message, sidebar selection and settings-adjacent
controls. A focused display test checks that Create, Rename and Add dialogs release
their controls after closing, and that emptied/deleted playlists and connection
resets clear cached covers. Audio was routed to a test sink during UI
validation, so audible playback and lower RAM were not evaluated or claimed.
Compilation and tests do not prove smooth long-running visuals or real-device
audio behavior.

## Current limits

Playlist sync beyond the shared Ember library, downloads, shuffle/repeat,
local filesystem scanning, session persistence and account sync remain out of
scope.
There is no disk artwork cache, so covers require network access on a new run.
The Rust client is the only desktop surface; provider account sync is not implemented.

Track three-dot menus in search and shared playlists include **Song radio**.
Last.fm chooses up to12 related tracks on the configured Ember backend; YouTube
Music supplies matched recordings. The native client uses the server address/key
from Ember Settings, places the selected song first, deduplicates recommendations,
and starts the new queue only after a nonempty successful response. No personal
Last.fm login is required, but the backend needs its API credentials and the new
`radio` action documented in [LASTFM.md](../mobile/backend/LASTFM.md).
Failed/empty recommendations leave the queue intact. Search, playback changes,
pause, stop or closing the app cancel pending radio responses. The mix is finite;
it does not automatically fetch more recommendations or fall back to YouTube mixes.

Home artwork uses a native height-for-width widget so covers fill the card and
remain square through initial display and resizing. The display-only regression
can be run in a native desktop session with:

```bash
cargo test --locked --manifest-path desktop-rust/Cargo.toml home_artwork_stays_square -- --ignored --test-threads=1 --nocapture
```

## Automatic audio caching and Settings

Click the Ember name/logo at the top left (or focus it and press Enter) to open
Settings. Automatic caching is on by default; it can be disabled there, and
Clear audio cache removes cached songs without touching Python desktop downloads.

Playback checks a private Rust-specific disk cache before resolving a network
stream. On a miss, playback streams immediately and a separate, cancellable
worker downloads a copy using the existing Python AudioStore. Replaying a
completed cached song uses a local file, including when the network is down.
Least-recently-used eviction keeps completed audio within 2 GiB. Audio stays in
files rather than being loaded wholesale into RAM. The cache lives under
`$XDG_CACHE_HOME/ember-player/rust-audio` (normally `~/.cache/ember-player/rust-audio`).
The enable preference persists there too. Disabling automatic caching stops
background downloads; existing copies can still play until cleared or evicted.

Only one download runs at a time. Selecting another song cancels the old cache
job. Cancelled partial files and orphaned media are reclaimed on the next cache
or clear operation, under an interprocess writer lock. Temporary/in-progress
files can exceed the completed-cache cap. Cache failures fall back to streaming;
a local playback error drops that cached file and retries through resolution.
The existing search/queue session is still required to select songs offline:
this is an evictable playback cache, not a persistent offline library. First
plays use a separate download in addition to the playback stream, so they use
extra bandwidth. No next-song prefetch or cache-size picker is included yet.

## Shared Ember playlists

Open Settings by clicking the Ember name/logo. Enter the same HTTPS server address
(including `/ember`) and existing access key used by the iPhone, then Save playlist
connection. HTTP is accepted only for loopback development servers. The desktop
requires backend **0.3.0 or newer**; updating source does not deploy that backend.
The hidden key is stored in an owner-only `0600` connection file under
`$XDG_DATA_HOME/ember-player/rust-library` (normally `~/.local/share/...`), outside
both the checkout and evictable audio cache. No provider login is needed.

The sidebar Playlists section supports multiple shared playlists and clearly
marks the selected one. Its **+** opens a New playlist dialog; Create or Enter
submits a 1–120-character name and Cancel or Escape dismisses it. Rename and
delete live in the selected playlist page's overflow menu; deletion requires
confirmation and affects all devices. Song reorder, remove and queue actions are
in each row's overflow menu. Add to playlist is available in search-result menus
and from the player bar's add button, showing the song title/artist and compact
playlist rows with an explicit Close action. Playback continues to use the
desktop's own resolution, GStreamer and audio cache. Large playlists initially display
100 rows and append the next batch near the bottom as you scroll, without paging
buttons or replacing previous rows. Visited rows remain until the page refreshes.
Playback uses the full ordered list, sharing one track vector
between row callbacks rather than copying it per row. Cover collages use at most
four unique current track-artwork URLs and never upload or define a custom cover.

Refresh runs on Library entry, foreground return while Library is visible, and
manually. Foreground refresh waits for the activating click and skips open menus
or dialogs, so playlist actions are not dismissed by a page rebuild. No realtime or periodic polling is used. Successful reads are privately
cached per normalized server address, retaining summaries and the last opened
playlist. When offline those reads are available for browsing/playback, but edits
are disabled. Uncached audio still needs the network.

Writes fetch current membership, preserve occurrence IDs and use expected revisions.
Conflicts require Refresh and reapplying the edit; stale snapshots are never
silently saved. Before sending a mutation the client atomically persists its exact
request/UUID. After a timeout/relaunch, Retry pending save resends that same command.
New edits are blocked until it is acknowledged or explicitly rejected. Switching
servers preserves the former server's pending record. A separate library worker
keeps requests off the UI thread; the access key is read from the private file,
not sent through music bridge commands or diagnostics. Auth redirects are refused.

Focused checks:

```bash
.venv/bin/python -m unittest discover -s desktop-rust -p test_library.py -v
cargo test --locked --manifest-path desktop-rust/Cargo.toml library::tests
cargo test --locked --manifest-path desktop-rust/Cargo.toml \
  library::tests::closed_dialog_callbacks_release_window -- --ignored --exact
```

These exercise a temporary local backend, metadata contracts and dialog release,
not the deployed laptop library. The display-only dialog test must run in its own
process under a native GTK session. Approved CSV match reports can be imported
with the root `import_ember_playlist.py` tool; see [TRANSFER.md](../TRANSFER.md).

The grey Shuffle action beside playlist Play starts the full playlist in a random
queue order, preserving duplicate occurrences and leaving the saved order unchanged.

### Last.fm

Settings now includes backend-hosted Last.fm authorization. Save the same Ember server connection used by your iPhone, then Connect, approve in the browser, and Finish authorization. Only advancing, non-buffering playback counts; seeks and pauses do not. See [backend setup and retry limits](../mobile/backend/LASTFM.md).

### Playlist keyboard navigation

With a playlist open, type a title or artist to jump
to a matching song without changing playback. Title prefixes take priority over
substring matches. Repeating a single letter cycles through matches; pause for
one second or press Escape to start a new query. Enter plays the focused song.
Matches include the entire playlist, loading later row batches when needed.
Text-entry fields and open menus retain their normal keyboard behavior.
