use crate::model::valid_video_id;
use serde::Deserialize;
use std::fmt;

pub const MAX_LYRIC_LINES: usize = 800;
pub const MAX_LYRIC_TEXT_CHARS: usize = 500;
pub const MAX_LYRIC_TIMESTAMP_MS: u64 = 24 * 60 * 60 * 1000;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum LyricsKind {
    Synced,
    Plain,
    Instrumental,
    Missing,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct LyricLine {
    pub text: String,
    #[serde(default)]
    pub start_ms: Option<u64>,
    #[serde(default)]
    pub end_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Eq, Deserialize)]
pub struct LyricsData {
    pub track_id: String,
    pub source: String,
    pub kind: LyricsKind,
    #[serde(default)]
    pub lines: Vec<LyricLine>,
    #[serde(default)]
    pub message: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LyricsError(pub &'static str);

impl fmt::Display for LyricsError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for LyricsError {}

impl LyricsData {
    pub fn validate(mut self) -> Result<Self, LyricsError> {
        if !valid_video_id(&self.track_id) {
            return Err(LyricsError("invalid lyric track"));
        }
        if self.source != "lrclib" {
            return Err(LyricsError("unsupported lyric source"));
        }
        if self.lines.len() > MAX_LYRIC_LINES {
            return Err(LyricsError("too many lyric lines"));
        }
        for line in &mut self.lines {
            line.text = line.text.trim().to_owned();
            if line.text.is_empty()
                || line.text.chars().count() > MAX_LYRIC_TEXT_CHARS
                || line.text.chars().any(char::is_control)
            {
                return Err(LyricsError("invalid lyric text"));
            }
            if let Some(start) = line.start_ms {
                if start > MAX_LYRIC_TIMESTAMP_MS {
                    return Err(LyricsError("invalid lyric timestamp"));
                }
                if let Some(end) = line.end_ms {
                    if end < start || end > MAX_LYRIC_TIMESTAMP_MS {
                        return Err(LyricsError("invalid lyric interval"));
                    }
                }
            } else if line.end_ms.is_some() {
                return Err(LyricsError("invalid lyric interval"));
            }
        }
        match self.kind {
            LyricsKind::Synced => {
                if self.lines.is_empty() || self.lines.iter().any(|line| line.start_ms.is_none()) {
                    return Err(LyricsError("invalid synchronized lyrics"));
                }
            }
            LyricsKind::Plain => {
                if self.lines.is_empty()
                    || self
                        .lines
                        .iter()
                        .any(|line| line.start_ms.is_some() || line.end_ms.is_some())
                {
                    return Err(LyricsError("invalid plain lyrics"));
                }
            }
            LyricsKind::Instrumental | LyricsKind::Missing => {
                if !self.lines.is_empty() {
                    return Err(LyricsError("invalid empty lyrics state"));
                }
            }
        }
        if let Some(message) = self.message.as_mut() {
            *message = message.trim().to_owned();
            if message.chars().count() > 240 || message.chars().any(char::is_control) {
                return Err(LyricsError("invalid lyric status"));
            }
        }
        Ok(self)
    }

    pub fn active_index(&self, position_ms: u64) -> Option<usize> {
        if self.kind != LyricsKind::Synced {
            return None;
        }
        self.lines
            .iter()
            .rposition(|line| line.start_ms.is_some_and(|start| start <= position_ms))
    }

    pub fn attribution(&self) -> &'static str {
        "Lyrics provided by LRCLIB"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn line(text: &str, start: Option<u64>, end: Option<u64>) -> LyricLine {
        LyricLine {
            text: text.to_owned(),
            start_ms: start,
            end_ms: end,
        }
    }

    fn synced() -> LyricsData {
        LyricsData {
            track_id: "dm4tkSNKfFI".to_owned(),
            source: "lrclib".to_owned(),
            kind: LyricsKind::Synced,
            lines: vec![
                line("One", Some(0), Some(1000)),
                line("Two", Some(1000), None),
            ],
            message: None,
        }
    }

    #[test]
    fn validates_and_finds_the_current_timed_line() {
        let lyrics = synced().validate().expect("valid lyrics");
        assert_eq!(lyrics.active_index(0), Some(0));
        assert_eq!(lyrics.active_index(999), Some(0));
        assert_eq!(lyrics.active_index(1000), Some(1));
        assert_eq!(lyrics.active_index(5000), Some(1));
        assert_eq!(lyrics.attribution(), "Lyrics provided by LRCLIB");
    }

    #[test]
    fn rejects_invalid_intervals_and_text() {
        let mut invalid = synced();
        invalid.lines[0].start_ms = Some(2000);
        invalid.lines[0].end_ms = Some(1000);
        assert!(invalid.validate().is_err());

        let mut control = synced();
        control.lines[0].text = "bad\nline".to_owned();
        assert!(control.validate().is_err());

        let mut too_long = synced();
        too_long.lines[0].text = "x".repeat(MAX_LYRIC_TEXT_CHARS + 1);
        assert!(too_long.validate().is_err());
    }

    #[test]
    fn plain_lines_are_not_seekable_and_empty_states_are_explicit() {
        let plain = LyricsData {
            track_id: "dm4tkSNKfFI".to_owned(),
            source: "lrclib".to_owned(),
            kind: LyricsKind::Plain,
            lines: vec![line("Words", None, None)],
            message: Some("No timestamps.".to_owned()),
        };
        let plain = plain.validate().expect("plain lyrics");
        assert_eq!(plain.active_index(10), None);

        for kind in [LyricsKind::Instrumental, LyricsKind::Missing] {
            let value = LyricsData {
                track_id: "dm4tkSNKfFI".to_owned(),
                source: "lrclib".to_owned(),
                kind,
                lines: Vec::new(),
                message: None,
            };
            assert!(value.validate().is_ok());
        }
    }

    #[test]
    fn timestamps_are_bounded() {
        let mut invalid = synced();
        invalid.lines[0].start_ms = Some(MAX_LYRIC_TIMESTAMP_MS + 1);
        assert!(invalid.validate().is_err());
    }
}
