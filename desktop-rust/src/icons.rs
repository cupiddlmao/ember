//! Bundled 24 px line icons for the desktop shell.
//!
//! GTK normally resolves `*-symbolic` names from the host icon theme, which
//! makes the app look different on Breeze, Adwaita, Papirus, and so on. These
//! SVGs are embedded in the binary and written to a private theme directory;
//! GTK is then pointed at that theme for this process only.

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
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 9.5h4L13 5v14l-5-4.5H4z"/><path d="M16 9a4.2 4.2 0 0 1 0 6"/><path d="M18.5 6.5a8 8 0 0 1 0 11"/></svg>"##,
    ),
    (
        "folder-music-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 7.5a2 2 0 0 1 2-2h4l2 2h7a2 2 0 0 1 2 2v7.5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2z"/><path d="M12.5 11.5v4.8"/><circle cx="10.9" cy="16.3" r="1.6"/><path d="m12.5 11.5 3.4-1v3.6"/><circle cx="14.3" cy="14.1" r="1.6"/></svg>"##,
    ),
    (
        "go-down-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>"##,
    ),
    (
        "go-home-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m3.5 10.5 8.5-7 8.5 7"/><path d="M5.5 9.5V20h13V9.5"/><path d="M9.5 20v-5h5v5"/></svg>"##,
    ),
    (
        "go-up-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="m6 15 6-6 6 6"/></svg>"##,
    ),
    (
        "list-add-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>"##,
    ),
    (
        "media-optical-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8"><circle cx="12" cy="12" r="8.5"/><circle cx="12" cy="12" r="2" fill="#2e3436" stroke="none"/></svg>"##,
    ),
    (
        "media-playback-pause-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><rect x="7" y="5" width="3.4" height="14" rx="1.2" fill="#2e3436"/><rect x="13.6" y="5" width="3.4" height="14" rx="1.2" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playback-start-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><path d="M8 5.2 19 12 8 18.8z" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playback-stop-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><rect x="6" y="6" width="12" height="12" rx="1.8" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-playlist-shuffle-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h4c4.5 0 6.5 10 11 10h1"/><path d="m17 4 3 3-3 3"/><path d="M4 17h4c2.3 0 3.7-2.6 5.2-5.3"/><path d="m17 14 3 3-3 3"/></svg>"##,
    ),
    (
        "media-skip-backward-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M5 5v14"/><path d="M19 6v12L9 12z" fill="#2e3436"/></svg>"##,
    ),
    (
        "media-skip-forward-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M19 5v14"/><path d="M5 6v12l10-6z" fill="#2e3436"/></svg>"##,
    ),
    (
        "system-search-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></svg>"##,
    ),
    (
        "text-x-generic-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M6.5 3.5h7l4 4v13h-11z"/><path d="M13.5 3.5v4h4"/><path d="M9 12h6M9 15.5h6"/></svg>"##,
    ),
    (
        "view-fullscreen-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3.5 9V3.5H9"/><path d="M15 3.5h5.5V9"/><path d="M20.5 15v5.5H15"/><path d="M9 20.5H3.5V15"/></svg>"##,
    ),
    (
        "view-list-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round"><circle cx="4.5" cy="6.5" r="1" fill="#2e3436" stroke="none"/><circle cx="4.5" cy="12" r="1" fill="#2e3436" stroke="none"/><circle cx="4.5" cy="17.5" r="1" fill="#2e3436" stroke="none"/><path d="M8 6.5h11M8 12h11M8 17.5h11"/></svg>"##,
    ),
    (
        "view-more-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none"><circle cx="5" cy="12" r="1.4" fill="#2e3436"/><circle cx="12" cy="12" r="1.4" fill="#2e3436"/><circle cx="19" cy="12" r="1.4" fill="#2e3436"/></svg>"##,
    ),
    (
        "view-refresh-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20 12a8 8 0 1 1-2.34-5.66"/><path d="M20 4v5h-5"/></svg>"##,
    ),
    (
        "view-restore-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3.5v5H3"/><path d="M16 3.5v5h5"/><path d="M21 16h-5v5"/><path d="M3 16h5v5"/></svg>"##,
    ),
    (
        "window-close-symbolic",
        r##"<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#2e3436" stroke-width="1.8" stroke-linecap="round"><path d="m6 6 12 12M18 6 6 18"/></svg>"##,
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
