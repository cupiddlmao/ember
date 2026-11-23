# ADR 0003 — Home content model: sparse-first, derived from real state

- Status: accepted
- Date: 2026-11-09
- Deciders: product owner (noah), design interview

## Context

Apple Music's Home looks rich because it has editorial rails, personalised
recommendations and large artwork. Ember has none of those and must not fake
them (standing rule from the earlier handoffs). Today Home opens with two
232 px letter-fallback playlist tiles and a four-column grid in which four
different remasters of one Beatles album appear as four separate cards.

A craft pass cannot fix a thin page by decorating it. The page has to be
composed for the data Ember actually has, and may derive new groupings from
that data.

## Decision

Home is composed from real state only, in this order, with empty sections
omitted rather than shown as placeholders:

1. **Lead surface.** If a shared playlist was opened during this run, Home
   leads with that playlist's hero: collage cover, name, count and duration,
   Play and Shuffle. Otherwise the lead is an honest search invitation that
   focuses the sidebar field.
2. **Shared playlists.** A single rail of playlist cards (200 px): the playlist
   opened in this run first, then the library's own order. Covers are collages
   of up to four unique track artwork URLs, with the letter fallback only when
   no cover exists. One playlist must not produce a lonely half-row: a single
   card pairs with the lead surface rather than sitting alone.
3. **This session.** Grouped by album when two or more session recordings share
   album + artist: one album card with a count badge instead of N duplicate
   cards. Ungrouped recordings follow as recording cards. Order is most recent
   first, stable for the run.
4. Nothing else. No recommendations, no charts, no "made for you", no static
   editorial modules.

Derived means: grouped, ordered, or composed from data already in memory or in
the shared library. It never means generated titles, invented artists, or
placeholder tracks.

## Consequences

- Home finally scales: four Beatles remasters become one album card, which
  frees width for the playlist rail.
- Album grouping needs album + artist metadata that the bridge already returns.
- The hero surface is the natural home for artwork tinting (ADR 0002).
- Empty first run stays a single honest panel; it is not filled with shelves.
- Any future section must earn its place with real data; "it looks empty" is
  not sufficient justification.
