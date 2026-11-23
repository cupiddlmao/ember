# Ember Rust desktop — first milestone

Build a separate native Linux desktop prototype in desktop-rust/ using Rust, GTK4 and GStreamer. Preserve the current dark/pink Ember direction. This is a staged migration, not a rewrite of every feature.

## Verified starting point
- engine/ was an unused Spotify Premium/librespot prototype and has been removed.
- The desktop is the Rust/GTK4 client in desktop-rust/. run.sh builds and launches it.
- Rust 1.99.1, Cargo 1.99.1, GTK4 4.24.1 and GStreamer 1.28.8 are installed.
- The installed app is a separate copy under ~/.local/share/ember-player. Do not change it.
- The checkout contains unrelated desktop/backend edits. Preserve them. No commits, pushes or deployments.

## First milestone only
Search -> select recording -> play/pause/stop, with title, artist, loading/error status and basic volume control. Use native Rust GTK4 and GStreamer playbin for playback. No browser, Electron or webview.

Keep YouTube integration in a small Python subprocess bridge temporarily:
- Import and reuse music.search(query) and music.resolve(track). Do not reimplement YouTube extraction or scrape it in Rust.
- Search returns a dictionary containing tracks. Canonical tracks have id, uri, name, artists, album and duration_ms.
- resolve returns (normalized_track, temporary_https_stream_url).
- Invoke the checkout's Python environment with explicit process arguments, never shell interpolation. Discover/configure the checkout path rather than assuming the launch working directory.
- Exchange bounded JSON; errors must be structured and sanitized. Do not display or log stream URLs, tokens or raw subprocess output. Temporary stream URLs stay in memory.
- Run blocking work outside GTK's main thread. Reject stale search/resolve completions so selecting another song or pressing Stop cannot trigger old playback.
- Bound pending work and cancel/clean up child processes on replacement/exit; do not accumulate workers on repeated clicks.
- Handle missing Python, dependencies, GStreamer plugins and unavailable songs visibly. No Google login required.

Preserve the iPhone/backend files. Do not share or migrate private config yet. Queue, lyrics, account sync, artwork cache, downloads, session restore and feature parity are later milestones; do not add placeholders for them.

## Delivery
Add a short README with build/run instructions, system requirements and the temporary Python bridge limitation. Pin dependencies with Cargo.lock.
Run cargo check (or the project's minimal relevant build), plus focused tests for bridge decoding/error handling and stale playback rejection. Use fake data for these tests.
Report what actually ran. Native window appearance and audible/live YouTube playback require separate validation; do not claim them from compilation.
Do not claim lower RAM without a measured comparison.
