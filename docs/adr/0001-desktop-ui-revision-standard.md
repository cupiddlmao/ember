# ADR 0001 — Desktop UI revision: scope and standard

- Status: accepted
- Date: 2026-11-09
- Deciders: product owner (noah), design interview

## Context

Ember has two clients: a native GTK4/Rust Linux desktop app (`desktop-rust/`)
and a SwiftUI iPhone app (`mobile/ios/`). The desktop shell has been extended
through several handoffs — artwork, queue, lyrics, shared playlists, radio,
audio cache — while its visual craft lagged behind its behaviour. The requested
goal is to revise the UI "until it's the best possible version".

The desktop is the only surface that can be built, tested and visually
inspected on this machine (GTK 4.24, GStreamer 1.28, Cargo 1.99; no Xcode).

## Decision

1. **Desktop first, iPhone after.** The revision targets `desktop-rust/`. The
   SwiftUI app receives the same visual language in a later phase, from source
   only, because it cannot be compiled or visually verified here.
2. **The deficiency is craft, not structure.** The information architecture is
   accepted as-is: Home / Search / Library destinations, the shared-playlist
   sidebar, the top player, and the queue/lyrics side panel stay.
3. **The standard is Apple Music desktop proportions and restraint**, executed
   the way Cider (cider.sh) executes it: artwork-led pages, a full-width
   playback bar, disciplined density, fluid but restrained motion. Apple marks,
   labels, artwork and editorial copy are never copied.
4. **A craft pass may add small capability** where the visual result genuinely
   requires it — for example artwork-derived page tinting or animated
   transitions. It must not invent content, add dead controls, or change
   playback, queue semantics, the Python bridge, library or data contracts.

## Consequences

- The revision is judged on type scale, surface hierarchy, spacing rhythm,
  icon weight, state design, motion and copy — not on new pages.
- Removing developer-facing chrome (permanent status strip, "Session" section
  label, duplicate page/playlist titles) is in scope; renaming destinations is
  not, unless a later ADR says so.
- Any new visual capability must be justified by a craft outcome and must not
  regress the existing GLib ownership, stale-result rejection, or bounded-I/O
  guarantees.
- Visual verification happens in a native session on this machine; claims about
  motion smoothness or audio are made only from observation.

## Resolved by

- Home density and derived content: [ADR 0003](0003-home-content-model.md).
- Type, surface, geometry, motion and artwork tinting: [ADR 0002](0002-visual-system.md).
- Appearance (dark only) and the definition of done: [ADR 0004](0004-craft-done.md).
