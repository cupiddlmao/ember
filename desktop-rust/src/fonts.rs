//! Bundled Inter Variable and its process-private fontconfig entry.
//!
//! GTK resolves `font-family` through fontconfig, so a bundled face is only
//! visible to the application if fontconfig is pointed at it before the first
//! font lookup. The font is embedded in the binary and materialised beside a
//! generated `fonts.conf`; the system configuration is included, not replaced.

use std::fs;
use std::path::{Path, PathBuf};

/// The family fontconfig reports for the bundled face; asserted by the
/// display test only.
#[cfg(test)]
pub const FAMILY: &str = "Inter Variable";

const FONT_BYTES: &[u8] = include_bytes!("../assets/InterVariable.ttf");

/// The generated configuration, including the system one and our directory.
pub fn fontconfig_xml(dir: &Path) -> String {
    let dir = escape_xml(&dir.to_string_lossy());
    format!(
        "<?xml version=\"1.0\"?>\n\
         <!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n\
         <fontconfig>\n\
         \x20 <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>\n\
         \x20 <dir>{dir}</dir>\n\
         \x20 <cachedir>{dir}/cache</cachedir>\n\
         </fontconfig>\n"
    )
}

fn escape_xml(value: &str) -> String {
    value
        .replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
}

/// Writes the font and configuration under `base`, returning the font path.
pub fn install_into(base: &Path) -> Result<PathBuf, String> {
    fs::create_dir_all(base).map_err(|error| format!("cannot create font directory: {error}"))?;
    let font = base.join("InterVariable.ttf");
    fs::write(&font, FONT_BYTES).map_err(|error| format!("cannot write bundled font: {error}"))?;
    fs::write(base.join("fonts.conf"), fontconfig_xml(base))
        .map_err(|error| format!("cannot write font configuration: {error}"))?;
    Ok(font)
}

/// Installs the font and points fontconfig at it for this process only.
pub fn install() -> Result<PathBuf, String> {
    let base = gtk4::glib::user_cache_dir().join("ember-player/fonts");
    let font = install_into(&base)?;
    let config = base.join("fonts.conf");
    // SAFETY: called from `main` before GTK initialises and before any other
    // thread exists, so no concurrent environment access can occur.
    unsafe {
        std::env::set_var("FONTCONFIG_FILE", &config);
    }
    Ok(font)
}

#[cfg(test)]
mod tests {
    use super::*;
    use gtk4::prelude::*;

    #[test]
    fn fontconfig_file_adds_the_private_directory() {
        let xml = fontconfig_xml(Path::new("/tmp/ember fonts"));
        assert!(xml.contains("<dir>/tmp/ember fonts</dir>"));
        assert!(xml.contains("ignore_missing=\"yes\">/etc/fonts/fonts.conf"));
    }

    #[test]
    fn install_writes_the_font_and_config() {
        let temp = tempfile::tempdir().expect("temp dir");
        let path = install_into(temp.path()).expect("install font");
        assert!(path.is_file());
        assert!(temp.path().join("fonts.conf").is_file());
    }

    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn inter_family_is_available() {
        install().expect("install bundled font");
        gtk4::init().expect("GTK display");
        let label = gtk4::Label::new(Some("Ember"));
        let map = label.pango_context().font_map().expect("font map");
        let families: Vec<String> = map
            .list_families()
            .iter()
            .map(|family| family.name().to_string())
            .collect();
        assert!(
            families.iter().any(|name| name == FAMILY),
            "Inter missing from Pango families"
        );
    }
}
