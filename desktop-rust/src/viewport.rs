//! Window sizing, including the optional size used by the UI matrix capture.

pub const DEFAULT_WIDTH: i32 = 1180;
pub const DEFAULT_HEIGHT: i32 = 760;
pub const MIN_WIDTH: i32 = 780;
pub const MIN_HEIGHT: i32 = 580;
pub const MAX_CAPTURE: i32 = 7680;
pub const CAPTURE_ENV: &str = "EMBER_UI_SIZE";
pub const PAGES: [&str; 3] = ["home", "search", "library"];
pub const PAGE_ENV: &str = "EMBER_UI_PAGE";
pub const PANELS: [&str; 3] = ["queue", "lyrics", "lyrics-fullscreen"];
pub const PANEL_ENV: &str = "EMBER_UI_PANEL";
pub const DIALOGS: [&str; 4] = ["settings", "create", "rename", "delete"];
pub const DIALOG_ENV: &str = "EMBER_UI_DIALOG";

/// Parses the `WIDTHxHEIGHT` or `WIDTH×HEIGHT` capture size.
///
/// Sizes below the window minimum or above the capture ceiling are rejected
/// rather than clamped, so a typo cannot silently produce a misleading capture.
pub fn parse_size(value: &str) -> Option<(i32, i32)> {
    let value = value.trim();
    let (width, height) = value
        .split_once('x')
        .or_else(|| value.split_once('×'))?;
    let width: i32 = width.trim().parse().ok()?;
    let height: i32 = height.trim().parse().ok()?;
    if !(MIN_WIDTH..=MAX_CAPTURE).contains(&width)
        || !(MIN_HEIGHT..=MAX_CAPTURE).contains(&height)
    {
        return None;
    }
    Some((width, height))
}

/// The initial page requested by a UI-matrix capture, defaulting to Home.
///
/// Only the three real destinations are accepted, so a typo cannot leave the
/// verification capture pointing at a page that does not exist.
pub fn capture_page() -> String {
    match std::env::var(PAGE_ENV).ok().as_deref() {
        Some(page) if PAGES.contains(&page) => page.to_owned(),
        _ => "home".to_owned(),
    }
}

/// The side-panel state a capture should open itself in.
///
/// The panel, the immersive lyrics view and the library dialogs are all behind
/// a click, and nothing in this checkout can move a real pointer, so a
/// verification capture has to ask for them at launch.
pub fn capture_panel() -> Option<String> {
    named_from_env(PANEL_ENV, &PANELS)
}

/// The dialog a capture should open at launch, if any.
pub fn capture_dialog() -> Option<String> {
    named_from_env(DIALOG_ENV, &DIALOGS)
}

fn named_from_env(key: &str, allowed: &[&str]) -> Option<String> {
    let value = std::env::var(key).ok()?;
    // An unknown name is ignored rather than guessed at, so a typo cannot
    // produce a capture that quietly shows something else.
    allowed.contains(&value.as_str()).then_some(value)
}

/// Reads the capture size from the environment, if it is valid.
pub fn capture_size() -> Option<(i32, i32)> {
    std::env::var(CAPTURE_ENV)
        .ok()
        .and_then(|value| parse_size(&value))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capture_panel_and_dialog_accept_only_real_names() {
        unsafe {
            std::env::set_var(PANEL_ENV, "lyrics-fullscreen");
            std::env::set_var(DIALOG_ENV, "rename");
        };
        assert_eq!(capture_panel().as_deref(), Some("lyrics-fullscreen"));
        assert_eq!(capture_dialog().as_deref(), Some("rename"));
        unsafe {
            std::env::set_var(PANEL_ENV, "lyric");
            std::env::set_var(DIALOG_ENV, "delete-playlist");
        };
        assert_eq!(capture_panel(), None);
        assert_eq!(capture_dialog(), None);
        unsafe {
            std::env::remove_var(PANEL_ENV);
            std::env::remove_var(DIALOG_ENV);
        };
    }

    #[test]
    fn capture_page_accepts_only_real_destinations() {
        unsafe { std::env::set_var(PAGE_ENV, "library") };
        assert_eq!(capture_page(), "library");
        unsafe { std::env::set_var(PAGE_ENV, "settings") };
        assert_eq!(capture_page(), "home");
        unsafe { std::env::remove_var(PAGE_ENV) };
    }

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
}
