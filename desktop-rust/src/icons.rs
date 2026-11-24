//! Bundled 24 px icons for the desktop shell.
//!
//! GTK normally resolves `*-symbolic` names from the host icon theme, which
//! makes the app look different on Breeze, Adwaita, Papirus, and so on. These
//! SVGs are embedded in the binary and written to a private theme directory;
//! GTK is then pointed at that theme for this process only.
//!
//! Every icon is drawn with filled paths only. Some GTK builds rasterize
//! symbolic icons with a renderer that honours `fill` but drops `stroke`,
//! which turned a stroked refresh arrow into a dot and erased stroked lines
//! entirely. Fills survive everywhere.

use gtk4::gdk;
use std::fs;
use std::path::Path;

const THEME_NAME: &str = "Ember";
const INDEX_THEME: &str = r##"[Icon Theme]
Name=Ember
Comment=Ember bundled symbolic icons
Inherits=Adwaita,hicolor
Directories=scalable/actions

[scalable/actions]
Size=16
MinSize=8
MaxSize=512
Type=Scalable
Context=Actions
"##;

const ICONS: &[(&str, &str)] = &[
    (
        "audio-volume-high-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M4 9h3.4L12.6 4.6v14.8L7.4 15H4z"/><path fill="#2e3436" d="M15.4 8.9a4.6 4.6 0 0 1 0 6.2l-1.4-1.3a2.8 2.8 0 0 0 0-3.6z"/><path fill="#2e3436" d="M18 6.4a8 8 0 0 1 0 11.2l-1.4-1.3a6.2 6.2 0 0 0 0-8.6z"/></svg>"##,
    ),
    (
        "folder-music-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" fill-rule="evenodd" d="M3.5 7.4a2 2 0 0 1 2-2h4.1l2 2h6.9a2 2 0 0 1 2 2v7.7a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2zm2 1.8v7.9h13V9.2h-7.6l-2-2H5.5z"/><path fill="#2e3436" d="M12.4 11.5h1.7v4.9h-1.7z"/><path fill="#2e3436" d="M12.4 11.5l4.2-1.1v1.8l-4.2 1.1z"/><circle cx="10.9" cy="16.4" r="1.6" fill="#2e3436"/><circle cx="15.1" cy="15.4" r="1.6" fill="#2e3436"/></svg>"##,
    ),
    (
        "go-down-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M12 17.2 5 10.2l1.5-1.5L12 14.2l5.5-5.5 1.5 1.5z"/></svg>"##,
    ),
    (
        "go-home-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M12 3.2 2.9 11.1l1.3 1.5L12 6.1l7.8 6.5 1.3-1.5z"/><path fill="#2e3436" d="M5.4 9.4h2v10.1h-2zM16.6 9.4h2v10.1h-2zM5.4 19.5h13.2v2H5.4z"/><path fill="#2e3436" d="M9.6 16.5h4.8v5H9.6z"/></svg>"##,
    ),
    (
        "go-up-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M12 6.8 5 13.8l1.5 1.5L12 9.8l5.5 5.5 1.5-1.5z"/></svg>"##,
    ),
    (
        "list-add-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M11 4.5h2V11h6.5v2H13v6.5h-2V13H4.5v-2H11z"/></svg>"##,
    ),
    (
        "media-optical-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" fill-rule="evenodd" d="M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zm0 1.9a6.6 6.6 0 1 1 0 13.2 6.6 6.6 0 0 1 0-13.2z"/><circle cx="12" cy="12" r="2" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playback-pause-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect x="7" y="5" width="3.4" height="14" rx="1.2" fill="#2e3436"/><rect x="13.6" y="5" width="3.4" height="14" rx="1.2" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playback-start-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path d="M8 5.2 19 12 8 18.8z" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playback-stop-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1.8" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playlist-shuffle-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M4 6.6h4.3c1.9 0 3.2 1.5 4.4 3.3l1.2 1.9c1 1.6 1.8 2.6 3 2.6h1.6v2h-1.6c-2.3 0-3.7-1.6-4.9-3.4l-1.2-1.9c-1-1.5-1.7-2.5-2.5-2.5H4z"/><path fill="#2e3436" d="M4 15.6h4.3c1 0 1.7-.7 2.4-1.7l1.1 1.7c-.9 1.2-2 2-3.5 2H4z"/><path fill="#2e3436" d="M16.2 4.4 21 7.6l-4.8 3.2z"/><path fill="#2e3436" d="M16.2 13.4 21 16.6l-4.8 3.2z"/></svg>"##,
    ),
    (
        "media-skip-backward-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M5 5h2v14H5z"/><path d="M19 6v12L9 12z" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-skip-forward-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M17 5h2v14h-2z"/><path d="M5 6v12l10-6z" fill="#2e3436"/></svg>"##,
    ),
    (
        "system-search-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" fill-rule="evenodd" d="M10.5 3.8a6.7 6.7 0 1 0 0 13.4 6.7 6.7 0 0 0 0-13.4zm0 1.9a4.8 4.8 0 1 1 0 9.6 4.8 4.8 0 0 1 0-9.6z"/><path fill="#2e3436" d="M15.3 13.9 21 19.6 19.6 21 13.9 15.3z"/></svg>"##,
    ),
    (
        // The Lyrics toggle. A document glyph read as "open a file", which is
        // what the borrowed text-x-generic icon looked like beside Queue.
        "lyrics-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" fill-rule="evenodd" d="M3.2 5.4A2.2 2.2 0 0 1 5.4 3.2h13.2a2.2 2.2 0 0 1 2.2 2.2v9.2a2.2 2.2 0 0 1-2.2 2.2H9.6l-4.1 3.4a1.1 1.1 0 0 1-1.8-.85V16.8h-.1a2.2 2.2 0 0 1-.2-1.1zm2.2.3v9.1h13.2V5.7z"/><path fill="#2e3436" d="M6.6 8.1h10.8v1.7H6.6zM6.6 11.3h6.6v1.7H6.6z"/></svg>"##,
    ),
    (
        "text-x-generic-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" fill-rule="evenodd" d="M6.5 3.5h7.3L18 7.7v12.8H6.5zm2 1.8v11.2h7.5V8.4h-3.4V5.3z"/><path fill="#2e3436" d="M13.6 3.9 18 8.3h-4.4z"/><path fill="#2e3436" d="M9 12h6v1.7H9zM9 15.4h6v1.7H9z"/></svg>"##,
    ),
    (
        "view-fullscreen-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M3.5 3.5h5.6v2H5.5v3.6h-2zM14.9 3.5h5.6v5.6h-2V5.5h-3.6zM18.5 14.9h2v5.6h-5.6v-2h3.6zM3.5 14.9h2v3.6h3.6v2H3.5z"/></svg>"##,
    ),
    (
        "view-list-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><circle cx="4.5" cy="6.5" r="1.6" fill="#2e3436"/><circle cx="4.5" cy="12" r="1.6" fill="#2e3436"/><circle cx="4.5" cy="17.5" r="1.6" fill="#2e3436"/><path fill="#2e3436" d="M8 5.6h11.5v1.8H8zM8 11.1h11.5v1.8H8zM8 16.6h11.5v1.8H8z"/></svg>"##,
    ),
    (
        "view-more-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><circle cx="5" cy="12" r="1.4" fill="#2e3436"/><circle cx="12" cy="12" r="1.4" fill="#2e3436"/><circle cx="19" cy="12" r="1.4" fill="#2e3436"/></svg>"##,
    ),
    (
        "view-refresh-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M19.99 9.09a8.5 8.5 0 1 0 0 5.82L18.2 14.26a6.6 6.6 0 1 1 0-4.52z"/><path fill="#2e3436" d="M18.2 9.2 22.8 12l-4.6 2.8z"/></svg>"##,
    ),
    (
        "view-restore-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M8 3.5h2v2H5.5v4.5h-2V4.5a1 1 0 0 1 1-1zM14 3.5h5.5a1 1 0 0 1 1 1v5.5h-2V5.5H14zM18.5 14h2v5.5a1 1 0 0 1-1 1H14v-2h4.5zM3.5 14h2v4.5H10v2H4.5a1 1 0 0 1-1-1z"/></svg>"##,
    ),
    (
        "window-close-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><path fill="#2e3436" d="M6.4 4.9 12 10.5l5.6-5.6 1.5 1.5L13.5 12l5.6 5.6-1.5 1.5L12 13.5l-5.6 5.6-1.5-1.5L10.5 12 4.9 6.4z"/></svg>"##,
    ),
];

/// Prepares the icon theme and selects it for this GTK display only.
pub fn install(display: &gdk::Display) {
    let root = gtk4::glib::user_cache_dir().join("ember-player/icons");
    if let Err(error) = materialize(&root) {
        eprintln!("Ember: cannot prepare bundled icons: {error}");
        return;
    }

    // The display-owned IconTheme is a singleton and rejects set_theme_name().
    // Select the theme through GtkSettings instead, keeping the override local
    // to this process. Re-check after the path is visible to GTK.
    let theme = gtk4::IconTheme::for_display(display);
    theme.add_search_path(root.as_path());
    let settings = gtk4::Settings::for_display(display);
    let previous = settings.gtk_icon_theme_name();
    settings.set_gtk_icon_theme_name(Some(THEME_NAME));
    let theme = gtk4::IconTheme::for_display(display);
    theme.add_search_path(root.as_path());
    if !theme.has_icon("media-optical-symbolic") {
        settings.set_gtk_icon_theme_name(previous.as_deref());
        eprintln!("Ember: bundled icon theme was not found; using the desktop theme");
    }
}

fn materialize(root: &Path) -> std::io::Result<()> {
    let theme = root.join(THEME_NAME);
    let actions = theme.join("scalable/actions");
    fs::create_dir_all(&actions)?;
    fs::write(theme.join("index.theme"), INDEX_THEME)?;
    for (name, svg) in ICONS {
        fs::write(actions.join(format!("{name}.svg")), svg)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    #[test]
    fn every_bundled_icon_is_unique_and_has_a_view_box() {
        let mut names = HashSet::new();
        for (name, svg) in ICONS {
            assert!(names.insert(*name), "duplicate icon name: {name}");
            assert!(
                svg.contains(r#"viewBox="0 0 24 24""#),
                "icon has no 24 px view box: {name}"
            );
        }
    }

    /// Some GTK builds rasterize symbolic icons with a renderer that drops
    /// strokes, which reduces a stroked arrow to a dot and erases stroked
    /// lines. Keep every icon fill-only so it renders the same everywhere.
    #[test]
    fn icons_paint_with_fills_only() {
        for (name, svg) in ICONS {
            assert!(
                !svg.contains("stroke"),
                "icon must not rely on strokes: {name}"
            );
            assert!(
                svg.contains(r##"fill="#2e3436""##),
                "icon paints nothing without a fill: {name}"
            );
        }
    }

    #[test]
    fn materializes_the_theme_with_every_icon() {
        let temp = tempfile::tempdir().expect("temporary icon directory");
        materialize(temp.path()).expect("write bundled icon theme");
        let actions = temp.path().join(THEME_NAME).join("scalable/actions");
        assert!(temp.path().join(THEME_NAME).join("index.theme").is_file());
        for (name, _) in ICONS {
            assert!(actions.join(format!("{name}.svg")).is_file(), "{name}");
        }
    }
}
