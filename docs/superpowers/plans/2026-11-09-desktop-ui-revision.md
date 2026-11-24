# Ember Desktop UI Revision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise the native GTK4 desktop client to the craft standard in `docs/ui-revision-brief.md` — one type scale, five surfaces, four radii, honest Home composition, expressive systematic motion — without changing playback, queue, bridge or library behaviour.

**Architecture:** Introduce four small pure modules (viewport, motion, tint, home) plus two GTK-side modules (fonts, notice, responsive), then migrate `style.css` onto a token layer and rebuild the chrome, pages, panel and dialogs against it. Business logic stays out of the UI files; every rule that can be a pure function with a unit test becomes one.

**Tech Stack:** Rust 2024 (rust-version 1.92), GTK 4.12+ API on runtime 4.24.1, gtk4-rs 0.11.5, GStreamer 1.24+, libsoup 3, Pango, Inter Variable (SIL OFL) bundled, ImageMagick `import` + `xprop` for captures on the live Wayland/X11 session.

**Spec:** `docs/ui-revision-brief.md` with ADRs `docs/adr/0001-desktop-ui-revision-standard.md` … `0004-craft-done.md`. Executors read both the plan and the spec.

## Global Constraints

- Rust `1.92+`; GTK `4.12+` API floor, runtime `4.24.1`; GStreamer `1.24+`; no new UI framework, webview or Electron.
- Do not modify `music.py`, `audio_cache.py`, `disk_cache.py`, `services.py`, `playlist_search.py`, `transfer_playlist.py`, `import_ember_playlist.py`, `mobile/**`, `.github/**`, or the Python bridge protocol.
- Preserve: queue occurrence IDs and duplicate independence; Play next/Add/Reorder/Remove/Clear; automatic advance once; previous-restart at 3 s; paused-preparation silence; stale search/resolve/lyrics/playback rejection; strong `AppUi` ownership in the GLib polling source; per-lane child process cancellation; 300 ms lyric follow animation and its cancellation paths; percentage volume conversion; control-character validation.
- Never log, display or persist stream URLs, access keys or raw subprocess output. Never place provider text into unescaped GTK markup.
- Exact values: default window `1180×760`, minimum `780×580`; sidebar `232`; side panel `360`; player bar `56`; pill max `380`, min `280`; type `11/12/13/15/19/26/34` (+`48` cover initial); weights `400/500/600`; radii `6/10/14/999`; motion `90/120/180/220/320/420` ms; contrast `7:1 / 4.5:1 / 3:1`; dark appearance only.
- Commits are local only. Do not push, deploy, install or replace the copy under `~/.local/share/ember-player`.
- Visual verification happens on the live session (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-1`). There is no Xvfb, xdotool or wmctrl.

## Review Focus

1. **Long, non-Latin or emoji names** in the hero, cards and now-playing: a reasonable user expects ellipsis with a full tooltip and a visible count badge, never clipped or overflowing text. Pinned by `playlist_initial_handles_wide_and_emoji_names` (Task 8) and the long-title captures (Task 11).
2. **Missing or unloadable artwork**: expects the designed fallback, no tint, and no stale tint from the previous track. Pinned by `empty_or_transparent_pixels_produce_no_tint` and `contrast_is_standard_and_clamped` (Task 9).
3. **780×580 with the side panel open and long metadata**: expects transport, close, queue and lyrics to stay reachable with no horizontal clipping. Pinned by `compact_player_threshold` (Task 6) and the minimum-size captures (Task 11).
4. **Rapid track changes while artwork or tint loads**: expects the tint/backdrop to apply only to the currently selected item. Pinned by `a_slot_only_accepts_its_latest_url` and `cancelled_tint_request_is_dropped` (Task 9).
5. **Reduce-motion enabled and keyboard-only use**: expects no animation-dependent state, visible focus, and every control reachable by Tab. Pinned by `disabled_animation_jumps_to_end` (Task 3) and the keyboard pass (Task 11).

---

## File Structure

- Create `desktop-rust/src/viewport.rs` — window size constants, capture-size parsing, env wiring.
- Create `desktop-rust/src/fonts.rs` — bundled Inter materialisation and process-private fontconfig registration.
- Create `desktop-rust/src/motion.rs` — duration tokens, easing curve, reduced-motion-aware progress.
- Create `desktop-rust/src/notice.rs` — transient notice widget and its dismissal lifetime.
- Create `desktop-rust/src/responsive.rs` — `ResponsiveBar` widget that reports allocated width.
- Create `desktop-rust/src/home.rs` — pure session grouping (album grouping, first index).
- Create `desktop-rust/src/tint.rs` — palette extraction and contrast clamping.
- Create `desktop-rust/assets/InterVariable.ttf` and `desktop-rust/assets/OFL.txt` — bundled typeface and licence.
- Modify `desktop-rust/src/main.rs` — register modules, install fonts before GTK.
- Modify `desktop-rust/src/app.rs` — constants, sidebar, titlebar/player, notice wiring, Home session render.
- Modify `desktop-rust/src/library.rs` — Home lead/rail, `PlaylistSummary`, hero, tint request, confirm dialogs.
- Modify `desktop-rust/src/artwork.rs` — tint cache and tint delivery on `ArtworkSlot`.
- Modify `desktop-rust/src/style.css` — full rewrite onto tokens.
- Modify `desktop-rust/README.md` — new layout, capture recipe, font/asset notes.
- Modify `docs/adr/0002-visual-system.md`, `0003-home-content-model.md`, `0004-craft-done.md` — record probe outcomes and capture recipe.

---

### Task 1: Verification viewport and capture recipe

**Files:**
- Create: `desktop-rust/src/viewport.rs`
- Modify: `desktop-rust/src/main.rs`, `desktop-rust/src/app.rs` (`activate`, lines ~90–105), `desktop-rust/README.md`
- Test: `desktop-rust/src/viewport.rs` (`#[cfg(test)] mod tests`)

**Interfaces:**
- Consumes: nothing.
- Produces: `pub const DEFAULT_WIDTH: i32 = 1180; pub const DEFAULT_HEIGHT: i32 = 760; pub const MIN_WIDTH: i32 = 780; pub const MIN_HEIGHT: i32 = 580; pub const MAX_CAPTURE: i32 = 7680; pub const CAPTURE_ENV: &str = "EMBER_UI_SIZE";`
- Produces: `pub fn parse_size(value: &str) -> Option<(i32, i32)>` (accepts `WxH` and `W×H`, trims, rejects either side `< MIN_*`, `> MAX_CAPTURE`, or zero) and `pub fn capture_size() -> Option<(i32, i32)>` (reads the env var).

- [x] **Step 1: Write the failing test**

```rust
#[test]
fn parses_capture_sizes_and_rejects_out_of_range() {
    assert_eq!(parse_size("1180x760"), Some((1180, 760)));
    assert_eq!(parse_size("780×580"), Some((780, 580)));
    assert_eq!(parse_size(" 800x600 "), Some((800, 600)));
    assert_eq!(parse_size("779x580"), None);
    assert_eq!(parse_size("1180x579"), None);
    assert_eq!(parse_size("0x600"), None);
    assert_eq!(parse_size("7690x600"), None);
    assert_eq!(parse_size("wide"), None);
}
```

- [x] **Step 2: Run it and confirm failure**

Run: `cd desktop-rust && cargo test --locked viewport::tests::parses_capture_sizes_and_rejects_out_of_range`
Expected: FAIL — `parse_size` not found.

- [x] **Step 3: Implement `viewport.rs` and wire it into `activate()`**

Replace the literals in the window builder with `viewport::capture_size().unwrap_or((DEFAULT_WIDTH, DEFAULT_HEIGHT))` and `window.set_size_request(MIN_WIDTH, MIN_HEIGHT)`.

- [x] **Step 4: Re-run the test**

Run: `cd desktop-rust && cargo test --locked viewport::tests`
Expected: PASS.

- [x] **Step 5: Prove a capture end to end**

```bash
cd desktop-rust
GDK_BACKEND=x11 EMBER_UI_SIZE=780x580 ./target/release/ember-rust &
sleep 3
wid=$(xprop -root _NET_CLIENT_LIST | grep -oE '0x[0-9a-f]+' | tail -1)
import -window "$wid" screenshots/matrix/780-home.png
```

Add this recipe to `README.md` under "Checks". Confirm the PNG is 780×580.

**Observed 2026-11-09:** `import -window` includes 25 px of CSD shadow per
side. A 780×580 window arrives as 830×630; crop with `+25+25` before checking
the size.

- [x] **Step 6: Commit**

```bash
git add desktop-rust/src/viewport.rs desktop-rust/src/main.rs desktop-rust/src/app.rs desktop-rust/README.md
git commit -m "test: add sized capture harness for UI verification"
```

---

### Task 2: Bundle Inter Variable

**Files:**
- Create: `desktop-rust/assets/InterVariable.ttf`, `desktop-rust/assets/OFL.txt`, `desktop-rust/src/fonts.rs`
- Modify: `desktop-rust/src/main.rs`, `desktop-rust/README.md`
- Test: `desktop-rust/src/fonts.rs` (unit + one ignored display test)

**Interfaces:**
- Consumes: `gtk4::glib::user_cache_dir()`.
- Produces: `pub const FAMILY: &str = "Inter";`
- Produces: `pub fn fontconfig_xml(dir: &Path) -> String` (XML-escapes the path, includes `/etc/fonts/fonts.conf`).
- Produces: `pub fn install_into(base: &Path) -> Result<PathBuf, String>` — writes `include_bytes!("../assets/InterVariable.ttf")` and `fonts.conf` under `base`, returns the font path.
- Produces: `pub fn install() -> Result<PathBuf, String>` — calls `install_into($XDG_CACHE_HOME/ember-player/fonts)` and sets `FONTCONFIG_FILE`.

- [x] **Step 1: Fetch the font and licence**

```bash
cd desktop-rust && mkdir -p assets
curl -fsSL https://rsms.me/inter/font-files/InterVariable.ttf -o assets/InterVariable.ttf
curl -fsSL https://raw.githubusercontent.com/rsms/inter/master/LICENSE.txt -o assets/OFL.txt
```

Confirm the TTF is a variable font (`fc-scan assets/InterVariable.ttf | grep -i variable`). Both files are committed.

- [x] **Step 2: Write the failing tests**

```rust
#[test]
fn fontconfig_file_adds_the_private_directory() {
    let xml = fontconfig_xml(Path::new("/tmp/ember fonts"));
    assert!(xml.contains("<dir>/tmp/ember fonts</dir>"));
    assert!(xml.contains("ignore_missing=\"yes\">/etc/fonts/fonts.conf"));
}

#[test]
fn install_writes_the_font_and_config() {
    let temp = tempfile::tempdir().expect("temp dir");
    let path = install_into(temp.path()).expect("install font");
    assert!(path.is_file());
    assert!(temp.path().join("fonts.conf").is_file());
}
```

- [x] **Step 3: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked fonts::`
Expected: FAIL — module not found.

- [x] **Step 4: Implement `fonts.rs`; call it first in `main()`**

`std::env::set_var` is `unsafe` in edition 2024: wrap it with a `// SAFETY:` comment explaining it runs single-threaded before GTK starts. On error, print `Ember: {error}` to stderr and continue on the system stack.

- [x] **Step 5: Run the unit tests**

Run: `cd desktop-rust && cargo test --locked fonts::`
Expected: PASS.

- [x] **Step 6: Prove the family actually resolves**

```rust
#[test]
#[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
fn inter_family_is_available() {
    gtk4::init().expect("GTK display");
    let label = gtk4::Label::new(Some("Ember"));
    let families: Vec<String> = label.pango_context().families().iter().map(|f| f.name().to_string()).collect();
    assert!(families.iter().any(|name| name == FAMILY), "Inter missing from Pango families");
}
```

Run: `cd desktop-rust && GDK_BACKEND=x11 cargo test --locked fonts::tests::inter_family_is_available -- --ignored --exact --test-threads=1`
Expected: PASS.

**Decision gate:** if this fails, try `@font-face { font-family: "Inter"; src: url("file:///…/InterVariable.ttf"); }` in the provider and re-run. If it still fails, stop: report the blocker, keep the system stack, and do not start Task 4. Record the outcome in `docs/adr/0002-visual-system.md` under Risks.

- [x] **Step 7: Commit**

```bash
git add desktop-rust/assets desktop-rust/src/fonts.rs desktop-rust/src/main.rs desktop-rust/README.md
git commit -m "feat: bundle Inter Variable for deterministic desktop type"
```

---

### Task 3: Motion tokens, easing and one surfaced transition

**Files:**
- Create: `desktop-rust/src/motion.rs`
- Modify: `desktop-rust/src/main.rs`, `desktop-rust/src/app.rs` (`activate` panel overlay, `AppUi.side_panel`, stack durations), `desktop-rust/src/style.css`, `docs/adr/0002-visual-system.md`
- Test: `desktop-rust/src/motion.rs`

**Interfaces:**
- Consumes: nothing.
- Produces: `pub const PRESS/HOVER/STATE/PAGE/TINT/IMMERSIVE: Duration` = 90/120/180/220/320/420 ms.
- Produces: `pub fn ease(t: f64) -> f64` — cubic-bezier(.2, 0, 0, 1), clamped to `[0,1]`.
- Produces: `pub fn eased_progress(elapsed: Duration, duration: Duration, animations: bool) -> f64` — returns `1.0` when `!animations` or `duration.is_zero()`.

- [x] **Step 1: Write the failing tests**

```rust
#[test]
fn easing_is_clamped_monotonic_and_exact_at_ends() {
    assert_eq!(ease(-1.0), 0.0);
    assert_eq!(ease(0.0), 0.0);
    assert_eq!(ease(1.0), 1.0);
    assert_eq!(ease(2.0), 1.0);
    let mut last = 0.0;
    for step in 0..=100 {
        let value = ease(step as f64 / 100.0);
        assert!(value >= last);
        last = value;
    }
}

#[test]
fn disabled_animation_jumps_to_end() {
    let half = Duration::from_millis(110);
    let full = Duration::from_millis(220);
    assert_eq!(eased_progress(half, full, false), 1.0);
    assert_eq!(eased_progress(full, full, true), 1.0);
    assert_eq!(eased_progress(Duration::ZERO, full, true), 0.0);
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked motion::`
Expected: FAIL.

- [x] **Step 3: Implement `motion.rs`**

Use a Newton iteration or a sampled look-up for the bezier; either is fine as long as the tests pin the endpoints and monotonicity.

- [x] **Step 4: Run the tests**

Run: `cd desktop-rust && cargo test --locked motion::`
Expected: PASS.

- [x] **Step 5: Surface motion once — panel reveal**

Wrap `build_side_panel().container` in a `gtk4::Revealer` (`SlideRight`, duration `PAGE`) added as the overlay; store the revealer in `AppUi.side_panel` and replace `set_visible(true/false)` calls with `set_reveal_child`. Set the page `Stack` crossfade duration from `motion::PAGE`. Probe whether GTK animates a CSS `transition: background-color 120ms`; record supported/unsupported in ADR 0002.

- [x] **Step 6: Verify visually and with tests**

Run: `cd desktop-rust && cargo test --locked && cargo build --release`
Expected: PASS. Capture panel open/close at 1180×760; under `gtk-enable-animations=false` the panel appears instantly.

- [x] **Step 7: Commit**

```bash
git add desktop-rust/src/motion.rs desktop-rust/src/main.rs desktop-rust/src/app.rs desktop-rust/src/style.css docs/adr/0002-visual-system.md
git commit -m "feat: add systematic motion tokens and panel reveal"
```

---

### Task 4: Token migration in `style.css`

**Files:**
- Modify: `desktop-rust/src/style.css` (whole file)
- Test: shell assertions (no unit tests for CSS)

**Interfaces:**
- Consumes: the values in ADR 0002.
- Produces: `@define-color canvas #1b1b1f;` `sidebar #242428` `raised #26262b` `overlay #2e2e34` `sunken #141416` `hairline rgba(255,255,255,0.07)` `hairline-strong rgba(255,255,255,0.12)` `text #f4f4f6` `text-2 #a6a6af` `text-3 #787881` `accent #ef5367` `accent-hover #fa6678`.

- [x] **Step 1: Add the token block and replace every colour reference**

Every selector uses `@canvas`, `@text`, etc. No literal hex outside the block.

- [x] **Step 2: Collapse type, weight and radius values**

Map size roles to `11/12/13/15/19/26/34` (keep `48` only for `.cover-initial`), weights to `400/500/600`, radii to `6/10/14/999`.

- [x] **Step 3: Fix the permanent focus ring**

Accent border only on `.sidebar-search:focus-within`; the idle field uses a hairline. Remove the always-on accent border.

- [x] **Step 4: Verify mechanically**

```bash
cd desktop-rust/src
grep -oE 'font-size: [0-9.]+px' style.css | sort -u          # 8 lines: 11,12,13,15,19,26,34,48
grep -oE 'font-weight: [0-9]+' style.css | sort -u           # 400,500,600
grep -oE 'border-radius: [0-9]+px' style.css | sort -u       # 6,10,14 (+999px handled separately)
grep -v '^@define-color' style.css | grep -cE '#[0-9a-fA-F]{6}'   # 0
```

- [x] **Step 5: Build and capture**

Run: `cd desktop-rust && cargo build --release`
Capture Home, Search, Library at 1180×760 and 780×580. Confirm nothing is unreadable and the search field is quiet when unfocused.

- [x] **Step 6: Commit**

```bash
git add desktop-rust/src/style.css
git commit -m "style: migrate desktop stylesheet onto the visual token system"
```

---

### Task 5: Sidebar structure and transient notices

**Files:**
- Create: `desktop-rust/src/notice.rs`
- Modify: `desktop-rust/src/app.rs` (`SIDEBAR_WIDTH`, `build_sidebar`, `AppUi`, `activate`, `set_status`, `set_loading_status`), `desktop-rust/src/main.rs`, `desktop-rust/src/style.css`
- Test: `desktop-rust/src/notice.rs`

**Interfaces:**
- Produces: `pub struct Notice { pub widget: gtk4::Box }` with `pub fn new() -> Self`, `pub fn show(&self, text: &str, error: bool)`, `pub fn show_progress(&self, text: &str)`.
- Produces: `pub fn dismiss_after(error: bool) -> Option<Duration>` — `None` for errors, `Some(4s)` otherwise.

- [x] **Step 1: Write the failing test**

```rust
#[test]
fn errors_persist_and_success_dismisses() {
    assert_eq!(dismiss_after(true), None);
    assert_eq!(dismiss_after(false), Some(Duration::from_secs(4)));
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked notice::`
Expected: FAIL.

- [x] **Step 3: Implement `notice.rs` and replace the status strip**

Delete `status_row`, `spinner` and `status_label` from `main`/`AppUi`; add the notice as a bottom-left overlay on `body_overlay`. Keep `fn set_status(&mut self, message: &str, error: bool)` as the single entry point so existing call sites do not change. A new notice clears the previous timeout. Route `set_loading_status` through `Notice::show_progress` so in-flight work is never auto-dismissed, and delete every remaining `spinner`/`status_label` reference.

- [x] **Step 4: Restructure the sidebar**

`SIDEBAR_WIDTH = 232`; remove the "Session" label; nav order Home → Search → Library; playlists section header gains 4 px top margin and the add control stays in its header. Keep accessible labels on every icon button. Remove the launch-time `search_entry.grab_focus()` so the idle sidebar shows no accent ring; Tab still reaches the field first.

- [x] **Step 5: Verify**

Run: `cd desktop-rust && cargo test --locked && cargo build --release`
Capture 1180×760 and 780×580; trigger a search error and a successful library refresh and confirm the notice appears bottom-left and dismisses only for success.

- [x] **Step 6: Commit**

```bash
git add desktop-rust/src/notice.rs desktop-rust/src/app.rs desktop-rust/src/main.rs desktop-rust/src/style.css
git commit -m "feat: replace the status strip with transient notices and rework the sidebar"
```

---

### Task 6: Player bar and responsive width reporting

**Files:**
- Create: `desktop-rust/src/responsive.rs`
- Modify: `desktop-rust/src/app.rs` (`build_player_bar`, `PlayerParts`, `activate`, `AppUi`), `desktop-rust/src/style.css`, `desktop-rust/src/main.rs`
- Test: `desktop-rust/src/responsive.rs`

**Interfaces:**
- Produces: `pub const COMPACT_WIDTH: i32 = 900; pub fn compact_player(width: i32) -> bool`.
- Produces: `pub struct ResponsiveBar` (extends `gtk4::Box`) with `pub fn new(orientation: gtk4::Orientation) -> Self` and `pub fn on_width(&self, callback: impl Fn(i32) + 'static)`; emits on allocation changes only.
- Produces: `PlayerParts { header: gtk4::HeaderBar, titlebar: ResponsiveBar, volume_box: gtk4::Box, progress_scale: gtk4::Scale, … }`.

- [x] **Step 1: Write the failing tests**

```rust
#[test]
fn compact_player_threshold() {
    assert!(compact_player(780));
    assert!(!compact_player(900));
    assert!(!compact_player(1180));
}
```

Add an ignored display test asserting `ResponsiveBar` reports 1180 then 780 after `set_default_size` and main-context iteration.

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked responsive::`
Expected: FAIL.

- [x] **Step 3: Implement `responsive.rs`**

Subclass `gtk4::Box`, override `WidgetImpl::size_allocate`, call the parent, then invoke callbacks when the width changed. Follow the existing `SquareArtwork` subclass pattern in `app.rs`.

- [x] **Step 4: Rebuild the titlebar**

`titlebar = ResponsiveBar::new(Vertical)` containing the existing `HeaderBar` (`min-height: 56`) and the progress scale as a 12 px strip with a 2 px trough. Remove the scale from the now-playing pill. `window.set_titlebar(Some(&player.titlebar))`. Keep the artwork, title, artist, elapsed/duration and volume controls.

- [x] **Step 5: Wire compact behaviour**

At `compact_player(width)`: hide `volume_box` and the elapsed/duration label, and shrink the pill request to `280`. At wider sizes restore them. Never hide Queue, Lyrics, Stop or the window close control. Then confirm a `780x580` capture crops to exactly 780×580 (the raw X11 window is 50 px larger from CSD shadow).

- [x] **Step 6: Verify**

Run: `cd desktop-rust && cargo test --locked && cargo build --release`
Capture at 1180×760, 900×760 and 780×580 with and without the panel. Confirm no clipped controls.

- [x] **Step 7: Commit**

```bash
git add desktop-rust/src/responsive.rs desktop-rust/src/app.rs desktop-rust/src/style.css desktop-rust/src/main.rs
git commit -m "feat: rebuild the player bar with responsive compact behaviour"
```

---

### Task 7: Home composition with derived session groups

**Files:**
- Create: `desktop-rust/src/home.rs`
- Modify: `desktop-rust/src/app.rs` (`refresh_home`, new `album_card`, `HomeParts`), `desktop-rust/src/library.rs` (`render_home`, `selected_summary`, `summary_from_state`), `desktop-rust/src/main.rs`, `desktop-rust/src/style.css`
- Test: `desktop-rust/src/home.rs`, `desktop-rust/src/library.rs` tests module

**Interfaces:**
- Produces: `pub struct SessionGroup { pub album: Option<String>, pub artist: String, pub tracks: Vec<Track>, pub cover_url: Option<String>, pub first_index: usize }`.
- Produces: `pub fn group_session(tracks: &[Track]) -> Vec<SessionGroup>` — groups by `(album, first artist)` only when album is non-empty and the group holds ≥ 2 recordings; preserves first-seen order; a grouped entry carries the index of its first recording.
- Produces: `pub struct PlaylistSummary { pub id: String, pub name: String, pub track_count: usize, pub cover_urls: Vec<String> }` and `pub fn summary_from_state(state: &LibraryState) -> Option<PlaylistSummary>`.
- Produces: `impl LibraryPane { pub fn selected_summary(&self) -> Option<PlaylistSummary> }`.

- [x] **Step 1: Write the failing grouping tests**

Add a test helper `fn track(id: &str, album: &str, artist: &str) -> Track` first.

```rust
#[test]
fn groups_album_duplicates_and_keeps_singles() {
    let tracks = vec![track("A", "Let It Be", "The Beatles"), track("B", "Let It Be", "The Beatles"), track("C", "", "Solo")];
    let groups = group_session(&tracks);
    assert_eq!(groups.len(), 2);
    assert_eq!(groups[0].tracks.len(), 2);
    assert_eq!(groups[0].album.as_deref(), Some("Let It Be"));
    assert_eq!(groups[0].first_index, 0);
    assert_eq!(groups[1].album, None);
    assert_eq!(groups[1].first_index, 2);
}

#[test]
fn does_not_merge_different_artists_or_empty_albums() {
    let tracks = vec![track("A", "Dive", "Tycho"), track("B", "Dive", "Someone Else"), track("C", "", "Tycho"), track("D", "", "Tycho")];
    assert_eq!(group_session(&tracks).len(), 4);
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked home::`
Expected: FAIL.

- [x] **Step 3: Implement `home.rs` and render groups**

`refresh_home` iterates `group_session(&self.recent_tracks)`; when `album.is_some()` build `album_card` (cover from the group's first artwork URL, title = album, caption = artist, count badge) whose click calls `activate_recent(group.first_index)`; otherwise reuse `recent_card`. Keep the section count label showing recordings, not groups.

- [x] **Step 4: Add the lead surface and playlist rail**

`library.rs::render_home` puts the selected playlist hero (name, count, Play/Shuffle) above the rail when `state.detail` is present; the rail stays a `FlowBox` of playlist cards, selected playlist first then backend order. Reuse `cover_urls` for collage covers. Omit the section when there are no playlists.

- [x] **Step 5: Add the summary tests**

```rust
#[test]
fn summary_reports_selected_playlist_and_deduped_covers() {
    let mut state = LibraryState::default();
    state.detail = Some(playlist_with_covers(&["https://i.ytimg.com/a", "https://i.ytimg.com/a", "https://i.ytimg.com/b"]));
    let summary = summary_from_state(&state).expect("summary");
    assert_eq!(summary.track_count, 3);
    assert_eq!(summary.cover_urls.len(), 2);
}
```

- [x] **Step 6: Verify**

Run: `cd desktop-rust && cargo test --locked`
Capture Home with two playlists and duplicate-album recordings at 1180 and 780. Confirm one card per album and no empty section.

- [x] **Step 7: Commit**

```bash
git add desktop-rust/src/home.rs desktop-rust/src/app.rs desktop-rust/src/library.rs desktop-rust/src/main.rs desktop-rust/src/style.css
git commit -m "feat: compose Home from real session groups and shared playlists"
```

---

### Task 8: Library hero without duplicate headings

**Files:**
- Modify: `desktop-rust/src/library.rs` (`LibraryPane` fields, `new` header, `render_page`, `render_overview`, `cover_body`/`fixed_cover` use), `desktop-rust/src/style.css`
- Test: `desktop-rust/src/library.rs` tests module

**Interfaces:**
- Consumes: `PlaylistSummary`/`selected_summary` from Task 7 (no signature change).
- Produces: `fn set_page_header(&self, visible: bool)` toggling the "Library" title and its subtitle row.
- Produces: `fn hero_cover(urls: &[String], name: &str, loader: &Rc<RefCell<ArtworkLoader>>) -> gtk4::Widget` using a `gtk4::AspectFrame` (ratio 1) + `.hero-cover`.

- [x] **Step 1: Write the failing test for wide/emoji names**

```rust
#[test]
fn playlist_initial_handles_wide_and_emoji_names() {
    assert_eq!(playlist_initial("東京"), "東");
    assert_eq!(playlist_initial("🎵 mix"), "🎵");
    assert_eq!(playlist_initial(""), "?");
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked library::tests::playlist_initial_handles_wide_and_emoji_names`
Expected: FAIL. Implement it with `name.chars().next()` and a `"?"` fallback for an empty name, then re-run to PASS.

- [x] **Step 3: Remove the duplicate hierarchy**

Hide the page title and the "Shared with your iPhone · no provider account needed" subtitle whenever a playlist detail is shown; keep the refresh control in a slim toolbar. Delete the "SHARED PLAYLIST" eyebrow.

- [x] **Step 4: Rebuild the hero**

`hero_cover` at 200 px via CSS `min-width/min-height`, `@media (max-width: 900px)` drops it to 160 px and the title to 26 px. Title class `.playlist-hero-title` is 34/600, ellipsized, with a tooltip; meta shows count and duration; Play, Shuffle and overflow keep their current behaviour and accessible labels. The hero stays horizontal at the minimum width.

- [x] **Step 5: Verify**

Run: `cd desktop-rust && cargo test --locked`
Capture a playlist page at 1180, 780, with a 100-character name and a CJK name. Confirm no clipped controls and no title above the hero.

- [x] **Step 6: Commit**

```bash
git add desktop-rust/src/library.rs desktop-rust/src/style.css
git commit -m "style: make the playlist hero the page title and fix wide-name handling"
```

---

### Task 9: Artwork tint on hero surfaces

**Files:**
- Create: `desktop-rust/src/tint.rs`
- Modify: `desktop-rust/src/artwork.rs`, `desktop-rust/src/library.rs` (hero tint), `desktop-rust/src/app.rs` (immersive backdrop), `desktop-rust/src/main.rs`, `desktop-rust/src/style.css`
- Test: `desktop-rust/src/tint.rs`, `desktop-rust/src/artwork.rs`

**Interfaces:**
- Produces: `pub struct Tint { pub base: [u8; 3], pub accent: [u8; 3] }`.
- Produces: `pub fn from_rgba(pixels: &[u8], width: u32, height: u32) -> Option<Tint>` (samples every 16th pixel, ignores alpha < 128; `None` for empty/invalid sizes).
- Produces: `pub fn contrast_ratio(a: [u8; 3], b: [u8; 3]) -> f64`; `pub fn clamp_for_contrast(colour: [u8; 3], background: [u8; 3], min: f64) -> [u8; 3]`; `pub fn css(tint: &Tint) -> String`.
- Produces: `impl ArtworkSlot { pub fn with_tint(picture: &gtk4::Picture, on_tint: Rc<dyn Fn(Tint)>) -> Self; fn accepts(&self, url: &str) -> bool; #[cfg(test)] pub fn for_test(url: &str) -> Self }`.
- Produces: `impl ArtworkLoader { pub fn request_tint(loader: &Rc<RefCell<Self>>, url: &str, callback: Rc<dyn Fn(Tint)>) -> u64; pub fn cancel_tint(loader: &Rc<RefCell<Self>>, token: u64); #[cfg(test)] fn begin_tint(&mut self, url: &str) -> u64; fn cancel_tint_token(&mut self, token: u64); #[cfg(test)] fn pending_tint_waiters(&self) -> usize }`.

- [x] **Step 1: Write the failing tint tests**

```rust
#[test]
fn contrast_is_standard_and_clamped() {
    assert!((contrast_ratio([255, 255, 255], [0, 0, 0]) - 21.0).abs() < 0.1);
    let clamped = clamp_for_contrast([250, 245, 180], [27, 27, 31], 4.5);
    assert!(contrast_ratio(clamped, [27, 27, 31]) >= 4.5);
}

#[test]
fn empty_or_transparent_pixels_produce_no_tint() {
    assert!(from_rgba(&[], 0, 0).is_none());
    assert!(from_rgba(&[0, 0, 0, 0], 1, 1).is_none());
    assert!(from_rgba(&[255, 0, 0, 255], 1, 1).is_some());
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked tint::`
Expected: FAIL.

- [x] **Step 3: Implement `tint.rs`**

Luminance via sRGB relative luminance; `clamp_for_contrast` moves lightness toward black or white until the ratio is met (bounded loop, no infinite search).

- [x] **Step 4: Extend the artwork loader**

Cache `(gdk::Texture, Tint)`; compute the tint inside the existing blocking decode closure; deliver only to slots that still `accepts(url)` and to tint waiters whose token was not cancelled. Add the stale-guard test:

```rust
#[test]
fn a_slot_only_accepts_its_latest_url() {
    let slot = ArtworkSlot::for_test("https://i.ytimg.com/a");
    assert!(slot.accepts("https://i.ytimg.com/a"));
    assert!(!slot.accepts("https://i.ytimg.com/b"));
}

#[test]
fn cancelled_tint_request_is_dropped() {
    let mut loader = ArtworkLoader::default();
    let token = loader.begin_tint("https://i.ytimg.com/a");
    loader.cancel_tint_token(token);
    assert_eq!(loader.pending_tint_waiters(), 0);
}
```

- [x] **Step 5: Apply the tint**

Library hero and immersive lyrics install one `gtk4::CssProvider` per surface and reload it with `tint::css(&tint)`; the hero class adds a 160° gradient at ≤ 35% strength, immersive at ≤ 25%. Library cancels the previous tint token before requesting a new one. No tint is applied when artwork is missing.

- [x] **Step 6: Verify**

Run: `cd desktop-rust && cargo test --locked`
Capture a bright cover and a near-black cover in Library and immersive lyrics. Confirm required text keeps its contrast and switching tracks quickly never leaves the previous tint.

- [x] **Step 7: Commit**

```bash
git add desktop-rust/src/tint.rs desktop-rust/src/artwork.rs desktop-rust/src/library.rs desktop-rust/src/app.rs desktop-rust/src/main.rs desktop-rust/src/style.css
git commit -m "feat: tint hero surfaces from artwork with contrast clamping"
```

---

### Task 10: Settings, dialogs and menus

**Files:**
- Modify: `desktop-rust/src/app.rs` (`build_settings_window`), `desktop-rust/src/library.rs` (`confirm_discard`, `confirm_delete`, `dialog_window`, `add_dialog`), `desktop-rust/src/style.css`
- Test: `desktop-rust/src/library.rs` tests module

**Interfaces:**
- Produces: `fn confirm_window(parent: &glib::WeakRef<gtk4::ApplicationWindow>, title: &str, body: &str, confirm_label: &str, destructive: bool, on_confirm: impl Fn() + 'static)`.
- Produces: `build_settings_window` keeps its current tuple return type; only width (480), resizable and internal layout change.

- [x] **Step 1: Write the failing display test**

```rust
#[test]
#[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
fn confirm_dialog_cancel_does_not_confirm() {
    gtk4::init().expect("GTK display");
    let app = gtk4::Application::builder().application_id("io.ember.DialogTest").build();
    app.register(None::<&gtk4::gio::Cancellable>).expect("register");
    let parent = gtk4::ApplicationWindow::new(&app);
    let called = Rc::new(Cell::new(false));
    let flag = called.clone();
    confirm_window(&parent.downgrade(), "Delete playlist", "This affects all devices.", "Delete", true, move || flag.set(true));
    // Find the transient window among the display's toplevels and close it without confirming.
    assert!(!called.get());
}
```

- [x] **Step 2: Run and confirm failure**

Run: `cd desktop-rust && cargo test --locked confirm_dialog_cancel_does_not_confirm -- --ignored --exact --test-threads=1`
Expected: FAIL — function not found.

- [x] **Step 3: Replace stock alerts**

Implement `confirm_window` on top of `dialog_window`, with Escape and Cancel closing without calling `on_confirm`, and the destructive primary carrying the destructive class. Call it from `confirm_discard` and `confirm_delete`. Keep the existing `closed_dialog_callbacks_release_window` test green.

- [x] **Step 4: Restyle Settings and dialogs**

Settings 480 px and resizable, three labelled sections (Playback, Shared library, About) with hairline dividers and 11 px consequence text. Dialogs 420–460 px, radius 14, 19/600 title. Menus 10 px radius, 30 px rows, destructive colour meeting the floor on `@overlay`.

- [x] **Step 5: Verify**

Run: `cd desktop-rust && cargo test --locked`
Capture Settings, New playlist, Rename, Delete confirm and Add-to-playlist at 1180 and 780.

- [x] **Step 6: Commit**

```bash
git add desktop-rust/src/app.rs desktop-rust/src/library.rs desktop-rust/src/style.css
git commit -m "style: bring settings, dialogs and menus onto the shared visual system"
```

---

### Task 11: State matrix, documentation and closeout

**Files:**
- Modify: `desktop-rust/README.md`, `docs/adr/0002-visual-system.md`, `docs/adr/0004-craft-done.md`
- Create: `screenshots/matrix/*.png`

**Interfaces:**
- Consumes: `EMBER_UI_SIZE` from Task 1; every surface from Tasks 4–10.
- Produces: the capture set and the final checklist record.

- [x] **Step 1: Run all suites**

```bash
python -m unittest discover -q
cd desktop-rust && cargo test --locked
cd desktop-rust && python3 -m unittest -v test_bridge.py test_cache.py test_library.py
```

Expected: all PASS. If shared code was untouched, root suites are still run once for the record.

- [x] **Step 2: Capture the matrix**

Sizes `1180x760`, `780x580`, `1440x900`; states: Home empty, Home populated, Search results, Library overview, Library hero, queue panel, lyrics panel (loading/synced/plain/error), immersive lyrics, Settings, each dialog, menu open, hover, focus-visible, long names, 5,000-row playlist. Use the Task 1 recipe; save each as `screenshots/matrix/<size>-<state>.png`.

- [x] **Step 3: Check the matrix**

Contrast floor on every surface including tinted ones; tab order reaches every control; icon-only controls keep tooltips and accessible labels; reduce-animation removes motion; 1.5× and 2× scale show no clipped text; no developer copy remains; no permanent chrome.

- [x] **Step 4: Fix and re-capture**

Fix defects in the owning task's files, rerun that task's tests, then re-capture only the affected states.

- [x] **Step 5: Update documentation**

README: new widths, player bar, notices, bundled Inter plus OFL, capture recipe, remaining limits. ADR 0002: record the font and CSS-transition probe outcomes. ADR 0004: record captures performed and anything not verified.

- [x] **Step 6: Commit**

```bash
git add screenshots/matrix desktop-rust/README.md docs/adr/0002-visual-system.md docs/adr/0004-craft-done.md
git commit -m "docs: record the verified desktop UI state matrix"
```

---

## Closeout

Every step above is complete and the suites pass at the time of writing
(`cargo test --locked`: 84 passed, 12 ignored; the 12 display tests pass one
process each per `docs/RELEASE.md`; clippy clean). Two places where the result
differs from this recipe, recorded so the plan is not read as literal:

- **Task 11 Step 2/6 — the matrix is not committed.** `screenshots/` is
  gitignored (`.gitignore`), so `git add screenshots/matrix` adds nothing. The
  captures are working evidence on disk, not deliverables in the repository.
- **Task 11 Step 3 — the 1.5×/2× text check cannot be met.** GTK 4 exposes no
  app-side text scale and Ember declares its type in px, so the UI does not
  grow with the desktop setting. Measured, and written up under *Captures* in
  `docs/adr/0004-craft-done.md` rather than marked passed.

Found and fixed while checking the matrix, beyond anything this plan asked for:
white labels on the accent fill sat under the contrast floor; the sidebar
selection indicator had never rendered (GTK clips an inset shadow to the
rounded corner); unreachable artwork left placeholder discs in the hero collage
and the sidebar; a destructive confirm was indistinguishable from a benign
primary action; six notices named the bridge process instead of the problem;
and a large playlist reported its length in five-digit minutes.

## Self-Review

**Spec coverage:** scope and standard → header + Tasks 1–11; type/surface/geometry/motion/tint → Tasks 2–4, 6, 9; Home model → Task 7; Library hero → Task 8; panel/immersive → Tasks 3, 9; Settings/dialogs/menus → Task 10; dark-only → Task 4; done criteria → Task 11. No spec section lacks a task.

**Step scan:** every step names a file, a signature or a command with expected output. No "handle edge cases" or TBD lines. Conditional branches exist only where the platform probe can fail (Task 2 font gate, Task 3 CSS-transition support), each with explicit pass/fail evidence and an explicit stop rule.

**Type consistency:** `viewport::capture_size` (Task 1) is used in Task 11; `motion::PAGE` (Task 3) is used by the panel reveal; `compact_player` (Task 6) is used by the player wiring and verified at 780 in Task 11; `SessionGroup`/`summary_from_state` (Task 7) are reused by Task 8; `Tint` (Task 9) is produced only there. No renamed symbols.

**Review Focus:** lines 1–5 map to `playlist_initial_handles_wide_and_emoji_names` (Task 8), `empty_or_transparent_pixels_produce_no_tint` / `contrast_is_standard_and_clamped` (Task 9), `compact_player_threshold` (Task 6) plus Task 11 captures, `a_slot_only_accepts_its_latest_url` / `cancelled_tint_request_is_dropped` (Task 9), and `disabled_animation_jumps_to_end` (Task 3) plus the Task 11 keyboard pass.

**Proportion:** the plan pins interfaces, values and assertions; bodies appear only for the two platform probes and the tint maths, which tests do not determine.
