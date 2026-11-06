use gtk4::prelude::*;
use std::path::PathBuf;

pub fn root() -> PathBuf {
    gtk4::glib::user_cache_dir().join("ember-player/rust-audio")
}

pub fn enabled() -> bool {
    enabled_in(&root())
}

fn enabled_in(root: &std::path::Path) -> bool {
    match std::fs::read(root.join("settings.json")) {
        Ok(bytes) => serde_json::from_slice::<serde_json::Value>(&bytes)
            .ok()
            .and_then(|v| v["enabled"].as_bool())
            .unwrap_or(false),
        Err(error) => error.kind() == std::io::ErrorKind::NotFound,
    }
}

pub fn set_enabled(enabled: bool) -> std::io::Result<()> {
    let root = root();
    std::fs::create_dir_all(&root)?;
    let temp = root.join("settings.tmp");
    std::fs::write(
        &temp,
        serde_json::to_vec(&serde_json::json!({"enabled": enabled}))?,
    )?;
    std::fs::rename(temp, root.join("settings.json"))
}

pub fn valid_local_uri(uri: &str) -> bool {
    valid_local_uri_in(uri, &root().join("audio"))
}

fn valid_local_uri_in(uri: &str, directory: &std::path::Path) -> bool {
    let Some(path) = gtk4::gio::File::for_uri(uri).path() else {
        return false;
    };
    let Ok(path) = path.canonicalize() else {
        return false;
    };
    let Ok(root) = directory.canonicalize() else {
        return false;
    };
    uri.starts_with("file:///")
        && path.starts_with(root)
        && path.is_file()
        && path.metadata().map(|m| m.len() > 0).unwrap_or(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn corrupt_or_missing_cache_settings_fail_soft() {
        let temp = tempfile::tempdir().unwrap();
        assert!(enabled_in(temp.path()));
        let settings = temp.path().join("settings.json");
        std::fs::write(&settings, b"{broken").unwrap();
        assert!(!enabled_in(temp.path()));
        assert_eq!(std::fs::read(&settings).unwrap(), b"{broken");
    }

    #[test]
    fn local_audio_must_be_nonempty_and_contained_after_symlink_resolution() {
        let temp = tempfile::tempdir().unwrap();
        let audio = temp.path().join("audio");
        std::fs::create_dir(&audio).unwrap();
        let good = audio.join("song with spaces.webm");
        std::fs::write(&good, b"audio").unwrap();
        let uri = gtk4::gio::File::for_path(&good).uri();
        assert!(valid_local_uri_in(&uri, &audio));
        let outside = temp.path().join("outside.webm");
        std::fs::write(&outside, b"audio").unwrap();
        assert!(!valid_local_uri_in(
            &gtk4::gio::File::for_path(&outside).uri(),
            &audio
        ));
        let link = audio.join("link.webm");
        std::os::unix::fs::symlink(&outside, &link).unwrap();
        assert!(!valid_local_uri_in(
            &gtk4::gio::File::for_path(&link).uri(),
            &audio
        ));
        std::fs::write(&good, b"").unwrap();
        assert!(!valid_local_uri_in(&uri, &audio));
        assert!(!valid_local_uri_in("file://remote/audio/song.webm", &audio));
        assert!(!valid_local_uri_in("https://evil.test/song", &audio));
    }
}
