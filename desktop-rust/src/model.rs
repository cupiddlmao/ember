use serde::{Deserialize, Serialize};
use std::fmt;

pub const MAX_SEARCH_TRACKS: usize = 20;
pub const MAX_ARTWORK_URL_CHARS: usize = 2048;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Track {
    pub id: String,
    pub uri: String,
    pub name: String,
    pub artists: Vec<String>,
    pub album: String,
    pub duration_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub artwork_url: Option<String>,
}

impl Track {
    pub fn validate(mut self) -> Result<Self, MetadataError> {
        if !valid_video_id(&self.id) {
            return Err(MetadataError("invalid recording identifier"));
        }
        let expected_uri = format!("https://music.youtube.com/watch?v={}", self.id);
        if self.uri != expected_uri {
            return Err(MetadataError("invalid recording link"));
        }
        self.name = clean_text(&self.name, 300)?;
        if self.artists.is_empty() || self.artists.len() > 8 {
            return Err(MetadataError("invalid recording artist"));
        }
        let mut artists = Vec::with_capacity(self.artists.len());
        for artist in self.artists {
            artists.push(clean_text(&artist, 200)?);
        }
        self.artists = artists;
        if self.album.chars().count() > 300 || self.album.chars().any(char::is_control) {
            return Err(MetadataError("invalid recording album"));
        }
        self.album = self.album.trim().to_owned();
        if self.duration_ms > 86_400_000 {
            return Err(MetadataError("invalid recording duration"));
        }
        if let Some(url) = self.artwork_url.take() {
            let url = url.trim();
            if !valid_artwork_url(url) {
                return Err(MetadataError("invalid recording artwork"));
            }
            self.artwork_url = Some(url.to_owned());
        }
        Ok(self)
    }

    pub fn artist_line(&self) -> String {
        self.artists.join(" · ")
    }

    pub fn duration_text(&self) -> String {
        let seconds = self.duration_ms.saturating_add(500) / 1000;
        format!("{}:{:02}", seconds / 60, seconds % 60)
    }
}

#[derive(Clone, PartialEq, Eq, Deserialize)]
pub struct ResolveData {
    pub track: Track,
    pub stream_url: String,
}

impl ResolveData {
    pub fn validate(mut self) -> Result<Self, MetadataError> {
        self.track = self.track.validate()?;
        if !valid_stream_url(&self.stream_url) && !crate::cache::valid_local_uri(&self.stream_url) {
            return Err(MetadataError("invalid audio stream"));
        }
        Ok(self)
    }
}

impl fmt::Debug for ResolveData {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ResolveData")
            .field("track", &self.track)
            .field("stream_url", &"[redacted]")
            .finish()
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MetadataError(pub &'static str);

impl fmt::Display for MetadataError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for MetadataError {}

fn clean_text(value: &str, limit: usize) -> Result<String, MetadataError> {
    let trimmed = value.trim();
    if trimmed.is_empty()
        || trimmed.chars().count() > limit
        || trimmed.chars().any(char::is_control)
    {
        return Err(MetadataError("invalid recording text"));
    }
    Ok(trimmed.to_owned())
}

pub fn valid_video_id(value: &str) -> bool {
    value.len() == 11
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

pub fn valid_artwork_url(value: &str) -> bool {
    if value.is_empty()
        || value.len() > MAX_ARTWORK_URL_CHARS
        || value
            .chars()
            .any(|character| character.is_control() || character.is_whitespace())
    {
        return false;
    }
    let Some(rest) = value.strip_prefix("https://") else {
        return false;
    };
    let authority = rest
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    if authority.is_empty() || authority.contains('@') || authority.contains(':') {
        return false;
    }
    matches!(
        authority.as_str(),
        "i.ytimg.com"
            | "img.youtube.com"
            | "lh3.googleusercontent.com"
            | "lh4.googleusercontent.com"
            | "lh5.googleusercontent.com"
            | "lh6.googleusercontent.com"
            | "yt3.ggpht.com"
            | "yt3.googleusercontent.com"
    )
}

fn valid_stream_url(value: &str) -> bool {
    if value.len() > 4096
        || value
            .chars()
            .any(|character| character.is_control() || character.is_whitespace())
    {
        return false;
    }
    let Some(rest) = value.strip_prefix("https://") else {
        return false;
    };
    let authority = rest
        .split(['/', '?', '#'])
        .next()
        .unwrap_or_default()
        .to_ascii_lowercase();
    if authority.is_empty() || authority.contains('@') {
        return false;
    }
    let host = if authority.starts_with('[') {
        authority
            .split(']')
            .next()
            .unwrap_or_default()
            .trim_start_matches('[')
    } else {
        authority.split(':').next().unwrap_or_default()
    };
    host == "googlevideo.com" || host.ends_with(".googlevideo.com")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track() -> Track {
        Track {
            id: "dm4tkSNKfFI".to_owned(),
            uri: "https://music.youtube.com/watch?v=dm4tkSNKfFI".to_owned(),
            name: "Awake".to_owned(),
            artists: vec!["Tycho".to_owned()],
            album: "Awake".to_owned(),
            duration_ms: 284_000,
            artwork_url: None,
        }
    }

    #[test]
    fn validates_a_canonical_track_and_duration() {
        let track = track().validate().expect("valid track");
        assert_eq!(track.artist_line(), "Tycho");
        assert_eq!(track.duration_text(), "4:44");
    }

    #[test]
    fn rejects_non_googlevideo_stream_urls() {
        let value = ResolveData {
            track: track(),
            stream_url: "https://evil.test/audio".to_owned(),
        };
        assert!(value.validate().is_err());
    }

    #[test]
    fn resolve_debug_never_prints_the_temporary_url() {
        let value = ResolveData {
            track: track(),
            stream_url: "https://audio.googlevideo.com/secret".to_owned(),
        };
        let debug = format!("{value:?}");
        assert!(debug.contains("[redacted]"));
        assert!(!debug.contains("googlevideo"));
        assert!(!debug.contains("secret"));
    }

    #[test]
    fn validates_only_canonical_https_artwork_hosts() {
        for url in [
            "https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg",
            "https://img.youtube.com/vi/dm4tkSNKfFI/mqdefault.jpg",
            "https://lh3.googleusercontent.com/cover=w320-h180",
            "https://yt3.ggpht.com/cover",
        ] {
            assert!(valid_artwork_url(url), "{url}");
        }
        for url in [
            "http://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg",
            "https://i.ytimg.com.evil.test/cover.jpg",
            "https://user@i.ytimg.com/cover.jpg",
            "https://i.ytimg.com:8443/cover.jpg",
            "https://example.com/cover.jpg",
        ] {
            assert!(!valid_artwork_url(url), "{url}");
        }
        let mut valid = track();
        valid.artwork_url = Some("https://i.ytimg.com/vi/dm4tkSNKfFI/mqdefault.jpg".into());
        assert!(valid.validate().is_ok());
        let mut invalid = track();
        invalid.artwork_url = Some("https://example.com/cover.jpg".into());
        assert!(invalid.validate().is_err());
    }
}
