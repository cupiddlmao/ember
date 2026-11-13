# ADR 0002 — Visual system: one typeface, five surfaces, four radii

- Status: accepted
- Date: 2026-11-09
- Deciders: product owner (noah), design interview
- Supersedes: the ad-hoc values in `desktop-rust/src/style.css`

## Context

Measured from the current stylesheet:

| Dimension | Now |
| --- | --- |
| Font sizes | 19 distinct values, 9.5 → 48 px, in 0.5 px steps |
| Font weights | 6 (500, 550, 600, 650, 700, 750) |
| Radii | 8 (4, 5, 6, 7, 8, 10, 12, 999) |
| Hex colours | 97 |
| Typeface | host-resolved Cantarell; nothing bundled |

That is not a system, and the reference standard (Apple Music desktop as
executed by Cider) is defined by exactly this kind of discipline.

## Decision

### Type

Bundle **Inter Variable** (SIL OFL) with the application and use it for every
surface, including the immersive lyrics window.

- **Seven sizes:** 11, 12, 13, 15, 19, 26, 34 px. 13 px is the UI base; 48 px
  remains only for the letter fallback inside cover art.
- **Three weights:** 400 regular, 500 medium, 600 semibold.
- **Numerals:** tabular (`tnum`) for every duration, timestamp and counter.
- **Case:** sentence case everywhere. No uppercase eyebrows, no letter-spaced
  section labels; the existing "SHARED PLAYLIST" eyebrow becomes a normal
  section label or disappears.
- **Roles:** page title 19/600 · hero title 34/600 (26/600 at the minimum
  width) · section title 15/600 · body 13/400 · row title 13/500 · secondary
  12/400 · meta 11/400 · lyric line 19/600 · plain lyric 15/400 · immersive
  lyric 34/600.

### Colour and surfaces

Dark only (see ADR 0004). Five surfaces, two hairlines, three text tiers.

| Token | Value | Use |
| --- | --- | --- |
| `canvas` | `#1b1b1f` | main content background |
| `sidebar` | `#242428` | sidebar, player bar |
| `raised` | `#26262b` | cards, now-playing pill, rows on hover |
| `overlay` | `#2e2e34` | side panel, popovers, menus, dialogs |
| `sunken` | `#141416` | immersive lyrics base |
| `hairline` | `rgba(255,255,255,.07)` | dividers, 1 px |
| `hairline-strong` | `rgba(255,255,255,.12)` | panel and dialog edges |
| `text` | `#f4f4f6` | titles, primary |
| `text-2` | `#a6a6af` | artists, secondary |
| `text-3` | `#787881` | metadata, disabled-adjacent |
| `accent` | `#ef5367` | primary action, active icon, progress fill |
| `accent-hover` | `#fa6678` | hover only |

Contrast floor: `text` ≥ 7:1 on every surface, `text-2` ≥ 4.5:1, `text-3` ≥ 3:1
and never for anything a user must read to act. Accent text only on `canvas`,
`sidebar` or `overlay` — never on artwork.

Artwork tinting (hero surfaces only) must clamp lightness/chroma so the same
floor holds on the tinted surface; a bright yellow cover cannot turn the hero
into a low-contrast page.

### Geometry

- **Radii:** 6 (controls, rows), 10 (cards, artwork), 14 (panel, dialogs),
  999 (pills). Nothing else.
- **Spacing:** 4 px grid. Page gutter 24 px. Section gap 24 px. Row heights
  32 (nav), 40 (compact), 52 (song).
- **Sidebar:** 232 px. Keeps 548 px of content at the 780 px minimum.
- **Side panel:** 360 px, inset 12 px.
- **Player bar:** 56 px; now-playing pill 380 px max, shrinking to 280 px
  before the volume control drops at the minimum width.

### Icons

One stroke language: 1.6 px at a 16 px optical size, round caps and joins.
Filled glyphs are allowed only for play, pause and stop, optically matched to
the stroke weight by size rather than by mixing styles.

### States and motion

Motion is expressive but systematic (user decision): hover 120 ms, press 90 ms,
menu/state 180 ms, page and panel 220 ms, artwork fade 180 ms, tint crossfade
320 ms, immersive enter 420 ms. One standard curve
`cubic-bezier(.2,0,0,1)`; exits accelerate. Every animation has an instant path
when `gtk-enable-animations` is false. No looping or decorative motion. Lyric
text geometry never animates.

## Consequences

- The redesign is a token migration first: 19 sizes → 7, 6 weights → 3,
  8 radii → 4, 97 hex values → 15 tokens.
- Bundling a font adds a binary asset plus its OFL licence file to the
  repository and a load path that must be proven (see Risks).
- Every selector in `style.css` is rewritten; no surface may keep a raw hex or
  fractional size.
- Cover letter fallbacks keep 48 px display type, which is outside the UI
  scale by design.

## Risks

1. **Font loading.** GTK's documented CSS property list covers `font-family`
   and `font-weight` but not `@font-face`. The load path must be proven before
   the type system is committed: try `@font-face` in the provider first, then a
   process-private fontconfig app-font config. If neither works on GTK 4.24,
   fall back to the system stack and keep the scale.
2. **CSS animation support** in GTK is partial. If transitions are unavailable,
   implement the specified durations with tick callbacks (as the lyric
   follow-scroll already does) rather than dropping the motion.
3. **Tint contrast** must be tested with near-white and near-black covers.
4. **Sidebar width** trades 28 px of content; the playlist hero must reflow at
   the 780 px minimum.

## Probe results (2026-11-09)

- **Font loading:** the bundled face registers as **"Inter Variable"**, not
  "Inter". A process-private `FONTCONFIG_FILE` whose generated config includes
  `/etc/fonts/fonts.conf` and adds the cache directory works; Pango resolves
  the family and the display test passes. `@font-face` was not needed and is
  not relied on.
- **Capture geometry:** `import -window` includes 25 px of CSD shadow per
  side. A 780×580 request arrives as 830×630, and `WM_NORMAL_HINTS` reports the
  same shadow-inclusive minimum. Crop captures by 25 px per side; the content
  minimum is the requested 780×580, not 830×630.
- **CSS transitions:** implemented. The hover (120 ms), press (90 ms) and
  selection (180 ms) transitions are declared in `style.css` beside the
  selectors that use them, so the stylesheet is the single source; `motion.rs`
  keeps only the durations Rust drives (`PAGE`, `STATE`, `TINT`, `IMMERSIVE`).
  The panel reveal keeps its `Revealer` on the `PAGE` token and the results
  stack on `STATE`.
- **Tick-driven motion:** GTK cannot transition a gradient, so the immersive
  tint crossfades by fading its layer with `motion::TINT` (320 ms) and the
  immersive window fades in over `motion::IMMERSIVE` (420 ms), both through
  `eased_progress`. The library hero's tint still swaps in one frame: it has no
  separate layer to fade, which is recorded as a known limitation.

## Out of scope

Light appearance (ADR 0004), user-selectable accent colours, theme plugins.
