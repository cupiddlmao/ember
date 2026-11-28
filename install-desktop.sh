#!/usr/bin/env bash
# Put Ember in the desktop's app list, so it starts from the icon instead of a
# terminal.
#
# The launcher entry points at a shim, the shim execs the binary in this
# checkout, and the checkout is what discovers core/music.py, .venv/bin/python and
# desktop-rust/bridge.py by walking up from the executable. Nothing here copies
# a binary or sets EMBER_CHECKOUT_ROOT: docs/RELEASE.md explains why a copied
# runtime bundle is the failure mode this shape exists to prevent.
#
# Safe to re-run. It rewrites the shim, the desktop entry and the icon, and it
# never touches the cache, the library or the cookies file.
set -euo pipefail

root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$root"

binary="$root/desktop-rust/target/release/ember-rust"
shim="$HOME/.local/bin/ember"
desktop="$HOME/.local/share/applications/io.ember.Player.desktop"
icon_svg="$root/packaging/ember.svg"
icon_fallback="$root/mobile/ios/EmberMobile/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png"
icon_name="io.ember.Player"

# EMBER_INSTALL_SKIP_BUILD exists for test_desktop_installer.py, which runs
# this script against a throwaway HOME to check what it writes. Building Rust
# inside a test would cost more than the assertion is worth; nothing else may
# set it, because an unbuilt binary is an installed launcher that cannot start.
if [ "${EMBER_INSTALL_SKIP_BUILD:-0}" != "1" ]; then
    cargo build --release --manifest-path desktop-rust/Cargo.toml
fi

mkdir -p "$HOME/.local/bin" "$HOME/.local/share/applications"

# Written to a temporary file first: a half-written shim would otherwise be
# launchable while it was still being generated.
cat > "$shim.tmp" <<'SHIM'
#!/bin/sh
# Written by __ROOT__/install-desktop.sh. Do not edit in place; re-run it.
#
# Execs the build in the checkout rather than a copy of it, so this launcher
# cannot drift behind `bash __ROOT__/run.sh`. The venv check is the same one
# run.sh does, because a stale .venv fails YouTube sign-in fetches in a way
# that looks like YouTube's fault.
cd "__ROOT__" || exit 1
. ./desktop-env.sh
ember_ensure_venv || :
ember_export_cookies

BIN="__ROOT__/desktop-rust/target/release/ember-rust"
if [ ! -x "$BIN" ]; then
  printf 'Ember is not built yet.\nRun: bash __ROOT__/run.sh\n' >&2
  exit 1
fi
exec "$BIN" "$@"
SHIM
sed "s|__ROOT__|$root|g" "$shim.tmp" > "$shim"
rm -f "$shim.tmp"
chmod 755 "$shim"

cat > "$desktop.tmp" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Ember
Comment=Music, shared playlists and synced lyrics
Exec="$shim"
Icon=$icon_name
Terminal=false
Categories=AudioVideo;Audio;Music;Player;
Keywords=music;player;lyrics;playlist;radio;
StartupNotify=true
StartupWMClass=io.ember.RustPreview
DESKTOP
mv "$desktop.tmp" "$desktop"
chmod 644 "$desktop"

# The product's own mark, as a glyph on a transparent field. The iOS asset is
# that glyph on a dark plate, which reads as a square inside a square in the
# taskbar; packaging/ember.svg is the same shape without the plate, in the
# app's own accent. A generic multimedia-player icon is worse still: it is how
# the launcher hid the fact that it pointed at a stale bundle for a whole
# release cycle.
theme_dir="$HOME/.local/share/icons/hicolor"
render="$(command -v rsvg-convert || command -v magick || true)"

# The scalable icon is what the entry's Icon= name resolves to, so it goes in
# whether or not this machine can also produce PNG sizes. Installing PNGs only
# is how the entry fell back to the generic player glyph on a host with no
# rasteriser: the name matched nothing, and freedesktop lookup then reached past
# this theme into the desktop's.
mkdir -p "$theme_dir/scalable/apps"
cp "$icon_svg" "$theme_dir/scalable/apps/$icon_name.svg"

if [ -n "$render" ]; then
  for size in 16 24 32 48 64 128 256 512; do
    target="$theme_dir/${size}x${size}/apps"
    mkdir -p "$target"
    case "$render" in
      *rsvg-convert) rsvg-convert -w "$size" -h "$size" "$icon_svg" -o "$target/$icon_name.png" ;;
      # ImageMagick renamed its CLI in v7 and still ships both names.
      *) magick -background none -size "${size}x${size}" "$icon_svg" "$target/$icon_name.png" ;;
    esac
  done
elif [ -f "$icon_fallback" ] && command -v convert >/dev/null; then
  # PNG sizes for themes that ignore scalable icons: the tiled iOS asset beats
  # no icon at all.
  for size in 16 24 32 48 64 128 256 512; do
    target="$theme_dir/${size}x${size}/apps"
    mkdir -p "$target"
    convert "$icon_fallback" -background none -resize "${size}x${size}" "$target/$icon_name.png"
  done
else
  printf 'Ember: no PNG rasteriser; installed the scalable icon only.\n' >&2
fi

# The user theme needs the system index before it can be cached; without it
# gtk-update-icon-cache reports "No theme index file" and the icon name then
# resolves only by luck.
if [ ! -f "$theme_dir/index.theme" ] && [ -f /usr/share/icons/hicolor/index.theme ]; then
  cp /usr/share/icons/hicolor/index.theme "$theme_dir/index.theme"
fi

command -v gtk-update-icon-cache >/dev/null &&
    gtk-update-icon-cache -q "$theme_dir" || :
command -v update-desktop-database >/dev/null &&
    update-desktop-database "$HOME/.local/share/applications" || :

if command -v desktop-file-validate >/dev/null; then
  desktop-file-validate "$desktop"
  printf 'Desktop entry validated.\n'
fi

printf 'Installed: %s\nLauncher: %s\nRuns: %s\n' "$desktop" "$shim" "$binary"
