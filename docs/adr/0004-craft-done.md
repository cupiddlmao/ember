# ADR 0004 — Craft definition of done and verification limits

- Status: accepted
- Date: 2026-11-09
- Deciders: product owner (noah), design interview

## Decision

The revision is done when a **verified state matrix** passes and the existing
suites stay green. Not when it "looks good at the default size".

### Scope of surfaces

Every user-visible surface is in scope: Home, Search, Library, the player bar,
the Queue and Lyrics panel, immersive lyrics, Settings, the New / Rename /
Delete / Add-to-playlist dialogs, popover menus, and every empty, loading,
error and offline state.

### Appearance

Dark only. One intentional dark appearance with a documented surface ladder,
rather than a second light system that cannot be verified in this pass. The
stylesheet may be organised around tokens so a light variant is a later change
of values, not a rewrite of selectors.

### State matrix

Sizes: 1180×760 (default), 780×580 (minimum), and one wide size ≥ 1440.

States per surface: empty, populated, loading, error, offline (library), menu
open, dialog open, hover, keyboard focus visible, long titles ellipsized, and
a 5,000-row playlist for scroll and jump behaviour.

Checks:

- contrast floor from ADR 0002 holds on every surface and on tinted artwork;
- tab order reaches every control, and icon-only controls keep tooltips and
  accessible labels;
- reduced-motion (`gtk-enable-animations=false`) removes animation without
  breaking layout or lyric following;
- high-DPI capture at 1.5× and 2× shows no clipped text or broken hairlines;
- no developer-facing copy remains in the product UI (status chatter,
  "no provider account needed", "Session" as a section name);
- persistent chrome is not reintroduced.

### Automated checks

- `cargo test --locked` in `desktop-rust/`
- `python3 -m unittest -v test_bridge.py` in `desktop-rust/`
- root suites from `.github/workflows/ci.yml` if shared code is touched (it
  should not be)
- new unit tests only where logic is new: tint clamping, motion-token mapping,
  and any derived Home grouping. Pure CSS changes get no tests.

### Environment limits (this machine)

- Wayland session (`DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-1`), `grim` and
  ImageMagick `import` available; **no** Xvfb, xdotool, wmctrl or headless
  compositor. Visual capture happens on the live desktop, so windows will
  appear on the owner's screen during verification.
- Window-size capture for the matrix needs a small display-only harness (an
  ignored GTK test that presents at a given size) rather than a window manager.
- Motion smoothness is observed, not benchmarked. No frame-time or RAM claims
  are made without measurement, and none are planned in this pass.
- Captures include 25 px of CSD shadow per side; crop by `+25+25` before
  measuring, or a 780×580 window reads as 830×630.
- Interactive states (open menus, dialogs, hover, keyboard focus) cannot be
  driven with the tools on this machine. They need manual interaction or an
  in-app capture script before the matrix can be called complete.

### iPhone phase

Same tokens, same derived-Home model, same motion rules with Reduce Motion
honoured. It cannot be compiled or captured on this Linux machine, so it ships
as reviewed source changes explicitly marked unverified until built in the iOS
workflow.

## Consequences

- The pass ends at the matrix, not at an open-ended "keep polishing".
- Any surface left at stock GTK styling is a visible failure, which is why
  Settings and destructive confirms are in scope.
- Screenshots become part of the deliverable, not a nicety.
