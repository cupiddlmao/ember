# Contributing

Three conventions this repository enforces. Each one exists because breaking it
cost a debugging session.

## Merge with rebase

`main` is protected: direct pushes are rejected, a pull request is required, and
three checks must pass — `Rust desktop`, `Python core, bridge and mobile backend`
and `iOS Swift checks`.

Merge with **Rebase**. History stays linear, and every commit on `main` is a
change that was tested on its own rather than a merge bubble nobody can read.

If a required check fails on infrastructure rather than on code, rerun the failed
jobs. Do not push an empty commit to nudge it.

## No attribution in commits

A commit message ends at its own last line. No `Co-Authored-By`, no "generated
with", no tool or assistant credit: this is the author's work, and the message
should describe the change and why it was made.

Prefer the reason over a restatement of the diff. "Bound the GTK install so a
stalled mirror fails in ten minutes" says something the diff cannot.

## Treat a failed apt install as infrastructure first

The Ubuntu package mirror stalls partway through the package **downloads**, not
during the index fetch, and apt has no useful transfer timeout of its own, so a
dead connection holds the step until something kills it.

- The Rust job caches the `.debs` and installs with `--no-download`, so a warm
  cache never touches the mirror. The cache key follows the package list, so
  changing that list is a deliberate cache miss.
- The install step is bounded at ten minutes, and each transfer is time-limited,
  so a stall now costs a rerun instead of a stuck run.
- A Rust job that dies in the install step has usually stalled on the network.
  Rerun the failed jobs before reading it as a code fault. Two failures at the
  same point are worth investigating.
