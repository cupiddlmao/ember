//! Home session grouping: album runs become single cards.

use crate::model::Track;
use std::collections::HashMap;

#[derive(Clone, Debug)]
pub struct SessionGroup {
    pub album: Option<String>,
    pub artist: String,
    pub tracks: Vec<Track>,
    pub cover_url: Option<String>,
    pub first_index: usize,
}

/// Groups recordings that share a non-empty album and first artist.
///
/// Groups keep their first-seen position and only become album groups when
/// they contain at least two recordings. Singles stay as recording cards.
pub fn group_session(tracks: &[Track]) -> Vec<SessionGroup> {
    let mut groups: Vec<SessionGroup> = Vec::new();
    let mut seen: HashMap<(String, String), usize> = HashMap::new();
    for (index, track) in tracks.iter().enumerate() {
        let artist = track.artists.first().cloned().unwrap_or_default();
        if !track.album.is_empty() {
            let key = (track.album.clone(), artist.clone());
            if let Some(&group) = seen.get(&key) {
                groups[group].tracks.push(track.clone());
                continue;
            }
            seen.insert(key, groups.len());
        }
        groups.push(SessionGroup {
            album: (!track.album.is_empty()).then(|| track.album.clone()),
            artist,
            tracks: vec![track.clone()],
            cover_url: track.artwork_url.clone(),
            first_index: index,
        });
    }
    for group in &mut groups {
        if group.album.is_some() && group.tracks.len() < 2 {
            group.album = None;
        }
        if group.cover_url.is_none() {
            group.cover_url = group
                .tracks
                .iter()
                .find_map(|track| track.artwork_url.clone());
        }
    }
    groups
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(id: &str, album: &str, artist: &str) -> Track {
        Track {
            id: id.into(),
            uri: format!("https://music.youtube.com/watch?v={id}"),
            name: format!("Song {id}"),
            artists: vec![artist.into()],
            album: album.into(),
            duration_ms: 180_000,
            artwork_url: None,
        }
    }

    #[test]
    fn groups_album_duplicates_and_keeps_singles() {
        let tracks = vec![
            track("A", "Let It Be", "The Beatles"),
            track("B", "Let It Be", "The Beatles"),
            track("C", "", "Solo"),
        ];
        let groups = group_session(&tracks);
        assert_eq!(groups.len(), 2);
        assert_eq!(groups[0].tracks.len(), 2);
        assert_eq!(groups[0].album.as_deref(), Some("Let It Be"));
        assert_eq!(groups[0].first_index, 0);
        assert_eq!(groups[1].album, None);
        assert_eq!(groups[1].first_index, 2);
    }

    #[test]
    fn does_not_merge_different_artists_or_empty_albums() {
        let tracks = vec![
            track("A", "Dive", "Tycho"),
            track("B", "Dive", "Someone Else"),
            track("C", "", "Tycho"),
            track("D", "", "Tycho"),
        ];
        assert_eq!(group_session(&tracks).len(), 4);
    }
}
