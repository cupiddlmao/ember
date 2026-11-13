# Ember desktop UI revision — craft brief

Status: design settled, implementation not started.
Decisions: [ADR 0001](adr/0001-desktop-ui-revision-standard.md) (scope and
standard) · [ADR 0002](adr/0002-visual-system.md) (type, surface, geometry,
motion) · [ADR 0003](adr/0003-home-content-model.md) (Home content) ·
[ADR 0004](adr/0004-craft-done.md) (done criteria).

## The target

Make the GTK4 desktop client read as a finished product at Apple Music
desktop's level of restraint, executed the way Cider does it: artwork-led
pages, a disciplined playback bar, honest density, expressive but systematic
motion. No Apple marks, labels, artwork or editorial copy. Behaviour, queue
semantics, playback, the Python bridge, the library and all data contracts stay
untouched.

## What is wrong today (measured)

| Defect | Evidence |
| --- | --- |
| No type system | 19 sizes (9.5–48 px), 6 weights (500–750), host-dependent Cantarell |
| No surface system | 97 hex values; sidebar `#2d2d30` vs canvas `#222225` differ by ~4% |
| No radius discipline | 8 radii from 4 px to 999 px |
| Permanent focus ring | the sidebar search field shows its accent border at all times, including first launch |
| Competing hierarchies | Library page title (20 px) stacked above playlist title (28 px), plus "Shared with your iPhone · no provider account needed" |
| Developer chrome in product | permanent bottom status strip ("Shared library updated."), "Session" as a section label, nav ordered Library → Home → Search |
| Two card metrics | 232 px playlist cards over 208 px session cards in one scroll |
| Duplicate content | four remasters of one album as four equal cards |
| Inconsistent icon weight | 1.8 px stroked icons beside filled transport glyphs at 15 px |
| Motion | 120 ms crossfades only; every hover, press and selection snaps |

## The system (summary of ADR 0002)

- Inter Variable, seven sizes, three weights, tabular numerals, sentence case.
- Five surfaces, two hairlines, three text tiers, one accent; contrast floors:
  7:1 primary, 4.5:1 secondary, 3:1 metadata.
- Radii 6 / 10 / 14 / 999; 4 px spacing grid; sidebar 232; panel 360; player
  56.
- One icon stroke language; filled only for transport.
- Motion: 90 / 120 / 180 / 220 / 320 / 420 ms on one curve, instant path under
  reduce-animation.

## Surface treatment

### Player bar (56 px)

Three optical zones: transport, now-playing pill, utilities. Transport buttons
28 px with a 32 px play button; pill 380 px max shrinking to 280 px; artwork
40 px with 6 px radius; title 13/500, artist 12/400; elapsed/duration 11 px
tabular. Progress becomes a 2 px full-bleed line along the bottom edge of the
bar, expanding to a 12 px scrub target on hover with a visible thumb; the pill
no longer carries its own scale. Volume hides below ~900 px; times collapse
before volume. Queue and Lyrics stay reachable at every size.

### Sidebar (232 px)

Brand row 56 px with the Ember mark and name. Search field 32 px with a 1 px
hairline, accent border only on focus. Navigation rows 32 px with 16 px icons
and a quiet selected fill plus a 2 px accent inset bar; "Session" is replaced by
nothing (destinations speak for themselves) and the order becomes
Home → Search → Library. Playlists: 44 px rows with 28 px covers (existing
artwork, not new data), name 12/500, count 11 px; the add control sits in the
section header, not beside every row.

### Home

Per ADR 0003: lead surface (playlist hero or honest invitation), one shared
playlist rail, and "This session" grouped by album. Sections are separated by
32 px, never by boxes. Cards are 10 px radius, artwork square, captions two
lines maximum with ellipsis; hover lifts the caption to `text` and fades in a
40 px play affordance over the artwork.

### Search

Header 19/600 with the result count inline. Rows 52 px: 40 px artwork, title
13/500, artist 12/400, duration 11 px tabular, hover reveals play. One section,
no invented grouping. Empty state is a single sentence and a focused field, not
a card.

### Library

When a playlist is open, the page title disappears — the hero *is* the title:
200 px collage cover (160 px at the minimum width), name 34/600 (26/600 at the
minimum width), count and duration 12 px, Play (accent, 6 px radius), Shuffle
(raised surface), overflow. The hero stays horizontal at 780 px. The developer
subtitle is deleted. Song rows: 20 px index in `text-3`, 40 px
artwork, title/artist, duration, overflow; 52 px tall; drag-free reorder via the
existing menu.

### Queue and Lyrics panel (360 px)

Inset 12 px, radius 14, `overlay` surface, `hairline-strong` edge, 52 px header
with a pinned close. Queue: current entry in a `raised` card, upcoming rows
40 px with index, reorder and remove on hover. Lyrics: 19/600 lines,
color-only active treatment, 48 px line rhythm, attribution 11 px in `text-3`.

### Immersive lyrics

Keep the mode. Base `#141416`, artwork backdrop at 8% opacity scaled 120% and
blurred behind a 180° scrim, tint from the cover (ADR 0002) at 25% strength.
Title 34/600, artist 15/500 in `accent`-tinted text, lyric 34/600, transport
as today but optically aligned to the new icon language. Enter 420 ms,
exit 220 ms; instant under reduce-animation. Escape and the visible Exit
control unchanged.

### Settings (480 px, resizable)

Sections: Playback (automatic cache toggle, clear cache), Shared library
(server, key, save, Last.fm authorization), About (version, build identity).
Labels 12/500 in `text-2` above controls; 8 px field radius; help text 11 px in
`text-3` that explains the consequence, not the implementation. No developer
copy. Destructive actions use the same dialog shell as the rest of the app
rather than the stock alert.

### Dialogs and menus

Dialogs: 420–460 px, radius 14, title 19/600, body 12/400, right-aligned
secondary/primary actions, Escape and Enter preserved. Menus: radius 10,
6 px padding, 30 px rows, hairline separators, destructive in a red that meets
the floor on `overlay`.

### Status and errors

Delete the permanent strip. Replace it with a transient notice that appears
only for progress and errors (bottom-left, 320 px max, `overlay`, 6 px radius,
4 s auto-dismiss for success, persistent until dismissed for errors) and is
announced to assistive technology. Setup failures keep a visible inline alert
at the top of the content area with the corrective action.

## Home derived data (ADR 0003)

- Lead: last opened playlist this run, else search invitation.
- Playlist rail: ordered by last opened/updated; collage cover from up to four
  unique track-artwork URLs; letter fallback only when there is no artwork.
- Session: group by album + artist when ≥ 2 recordings match; one card with a
  count badge. Most recent first.
- Omit empty sections; never show more than three sections.

## Out of scope

Light mode, theme plugins, playlist sync changes, provider accounts, downloads,
shuffle/repeat beyond the existing playlist action, session persistence, MPRIS
changes, bridge or backend contracts, `mobile/ios` in this phase.

## Verification (ADR 0004)

Matrix at 1180×760, 780×580 and ≥ 1440; states: empty, populated, loading,
error, offline, menu, dialog, hover, focus-visible, long titles, 5,000-row
playlist. Contrast and tab order checked. Reduce-animation path checked.
`cargo test --locked` and the bridge tests green. Captures via `grim` on the
live Wayland session; no headless compositor is available.

## Build order

1. **Prove the two platform risks** — bundled font loading and CSS/tick-based
   motion — before committing either.
2. **Tokens** — replace raw values in `style.css` with the surface, type,
   radius and motion tokens; no visual regressions yet.
3. **Chrome** — player bar, sidebar, status strip removal, page titles.
4. **Pages** — Home composition and album grouping, Search rows, Library hero.
5. **Panel and immersive** — queue, lyrics, tint.
6. **Settings, dialogs, menus** — including destructive confirms.
7. **Matrix pass** — capture, compare, fix, re-capture.

## iPhone phase

Same tokens and Home model in SwiftUI, with Reduce Motion honoured and Dynamic
Type respected. Source-only changes here; built and verified through the iOS
workflow on macOS, and reported as unverified until then.
