use crate::queue::EntryId;

/// Outcome of an asynchronous resolve. Only the currently selected occurrence
/// can produce `Start` or `Ready`; every older response is `Stale`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ResolveDisposition {
    Start,
    Ready,
    Stale,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayAction {
    None,
    Resolve,
    StartPrepared,
    Resume,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PauseAction {
    None,
    CancelPending,
    PauseGst,
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
enum Phase {
    #[default]
    Idle,
    Preparing,
    Ready,
    Starting,
    Playing,
    Paused,
    Failed,
    Finished,
}

/// Playback intent and readiness for the selected queue occurrence.
///
/// This intentionally contains no GTK or GStreamer objects. The UI delegates
/// selection, stop/retry, preparation, pause and end-of-stream decisions here
/// so stale or paused work follows the same rules as the real player.
#[derive(Debug, Default)]
pub struct PlaybackState {
    current_entry: Option<EntryId>,
    track_id: Option<String>,
    resolve_seq: u64,
    phase: Phase,
    desired_playing: bool,
    eos_handled: bool,
}

impl PlaybackState {
    pub fn begin(&mut self, entry: EntryId, track_id: impl Into<String>, resolve_seq: u64) {
        self.current_entry = Some(entry);
        self.track_id = Some(track_id.into());
        self.resolve_seq = resolve_seq;
        self.phase = Phase::Preparing;
        self.desired_playing = true;
        self.eos_handled = false;
    }

    pub fn accept_resolve(&mut self, seq: u64, track_id: &str) -> ResolveDisposition {
        if self.phase != Phase::Preparing
            || seq != self.resolve_seq
            || self.track_id.as_deref() != Some(track_id)
        {
            return ResolveDisposition::Stale;
        }
        self.phase = Phase::Ready;
        if self.desired_playing {
            ResolveDisposition::Start
        } else {
            ResolveDisposition::Ready
        }
    }

    /// The caller has successfully set the player URI and asked GStreamer to
    /// start. The controller still waits for the playbin's own Playing state.
    pub fn mark_starting(&mut self) {
        if self.phase == Phase::Ready || self.phase == Phase::Paused {
            self.phase = Phase::Starting;
        }
    }

    pub fn on_playing(&mut self) -> bool {
        if !matches!(self.phase, Phase::Starting | Phase::Playing) {
            return false;
        }
        if self.desired_playing {
            self.phase = Phase::Playing;
            true
        } else {
            self.phase = Phase::Paused;
            false
        }
    }

    pub fn pause(&mut self) -> PauseAction {
        if !self.desired_playing {
            return PauseAction::None;
        }
        match self.phase {
            Phase::Preparing => {
                self.desired_playing = false;
                PauseAction::CancelPending
            }
            Phase::Starting | Phase::Playing => {
                self.desired_playing = false;
                self.phase = Phase::Paused;
                PauseAction::PauseGst
            }
            _ => {
                self.desired_playing = false;
                PauseAction::None
            }
        }
    }

    pub fn play(&mut self) -> PlayAction {
        match self.phase {
            Phase::Preparing => {
                self.desired_playing = true;
                PlayAction::None
            }
            Phase::Ready => {
                self.desired_playing = true;
                PlayAction::StartPrepared
            }
            Phase::Paused => {
                self.desired_playing = true;
                self.phase = Phase::Starting;
                PlayAction::Resume
            }
            Phase::Starting | Phase::Playing => {
                self.desired_playing = true;
                PlayAction::None
            }
            Phase::Idle | Phase::Failed | Phase::Finished => {
                self.desired_playing = true;
                PlayAction::Resolve
            }
        }
    }

    pub fn stop(&mut self) {
        self.phase = Phase::Idle;
        self.desired_playing = false;
        self.resolve_seq = 0;
        self.eos_handled = false;
    }

    pub fn fail(&mut self) {
        self.phase = Phase::Failed;
        self.desired_playing = false;
        self.resolve_seq = 0;
        self.eos_handled = false;
    }

    pub fn finish(&mut self) {
        self.phase = Phase::Finished;
        self.desired_playing = false;
        self.resolve_seq = 0;
        self.eos_handled = true;
    }

    pub fn eos_begin(&mut self) -> bool {
        if self.eos_handled
            || !matches!(self.phase, Phase::Starting | Phase::Playing | Phase::Paused)
        {
            return false;
        }
        self.eos_handled = true;
        true
    }

    #[cfg(test)]
    pub fn current_entry(&self) -> Option<EntryId> {
        self.current_entry
    }

    pub fn is_preparing(&self) -> bool {
        self.phase == Phase::Preparing
    }

    #[cfg(test)]
    pub fn has_prepared(&self) -> bool {
        self.phase == Phase::Ready
    }

    pub fn is_playing(&self) -> bool {
        self.phase == Phase::Playing
    }

    #[cfg(test)]
    pub fn is_paused(&self) -> bool {
        self.phase == Phase::Paused
    }

    pub fn loading(&self) -> bool {
        matches!(self.phase, Phase::Preparing | Phase::Starting)
    }

    pub fn stream_loaded(&self) -> bool {
        matches!(self.phase, Phase::Starting | Phase::Playing | Phase::Paused)
    }

    pub fn wants_play(&self) -> bool {
        self.desired_playing
    }

    pub fn can_stop(&self) -> bool {
        matches!(
            self.phase,
            Phase::Preparing | Phase::Ready | Phase::Starting | Phase::Playing | Phase::Paused
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Track;
    use crate::queue::Queue;

    fn track(id: &str, name: &str) -> Track {
        Track {
            id: id.to_owned(),
            uri: format!("https://music.youtube.com/watch?v={id}"),
            name: name.to_owned(),
            artists: vec!["Artist".to_owned()],
            album: String::new(),
            duration_ms: 180_000,
            artwork_url: None,
        }
    }

    #[test]
    fn stale_resolve_is_rejected_even_for_the_same_recording() {
        let mut playback = PlaybackState::default();
        playback.begin(1, "dm4tkSNKfFI", 10);
        assert_eq!(
            playback.accept_resolve(10, "dm4tkSNKfFI"),
            ResolveDisposition::Start
        );

        playback.begin(2, "dm4tkSNKfFI", 11);
        assert_eq!(
            playback.accept_resolve(10, "dm4tkSNKfFI"),
            ResolveDisposition::Stale
        );
        assert_eq!(
            playback.accept_resolve(11, "dm4tkSNKfFI"),
            ResolveDisposition::Start
        );
    }

    #[test]
    fn pausing_during_preparation_cancels_autoplay_but_keeps_the_resolve() {
        let mut playback = PlaybackState::default();
        playback.begin(1, "dm4tkSNKfFI", 4);
        assert_eq!(playback.pause(), PauseAction::CancelPending);
        assert!(!playback.wants_play());
        assert!(playback.is_preparing());
        assert_eq!(
            playback.accept_resolve(4, "dm4tkSNKfFI"),
            ResolveDisposition::Ready
        );
        assert!(playback.has_prepared());
        assert!(!playback.stream_loaded());
        assert_eq!(playback.play(), PlayAction::StartPrepared);
    }

    #[test]
    fn stop_preserves_selection_for_retry_and_rejects_old_audio_events() {
        let mut playback = PlaybackState::default();
        playback.begin(7, "dm4tkSNKfFI", 3);
        assert_eq!(
            playback.accept_resolve(3, "dm4tkSNKfFI"),
            ResolveDisposition::Start
        );
        playback.mark_starting();
        assert!(playback.on_playing());
        playback.stop();
        assert_eq!(playback.current_entry(), Some(7));
        assert_eq!(playback.play(), PlayAction::Resolve);
        assert_eq!(
            playback.accept_resolve(3, "dm4tkSNKfFI"),
            ResolveDisposition::Stale
        );
        assert!(!playback.eos_begin());
    }

    #[test]
    fn paused_starting_state_causes_the_late_playing_event_to_be_paused() {
        let mut playback = PlaybackState::default();
        playback.begin(1, "dm4tkSNKfFI", 1);
        assert_eq!(
            playback.accept_resolve(1, "dm4tkSNKfFI"),
            ResolveDisposition::Start
        );
        playback.mark_starting();
        assert_eq!(playback.pause(), PauseAction::PauseGst);
        assert!(!playback.on_playing());
        assert!(playback.is_paused());
    }

    #[test]
    fn queue_selection_survives_stop_and_can_be_retried() {
        let mut queue = Queue::default();
        queue.replace_search(&[track("aaaaaaaaaaa", "A")], 0);
        let entry = queue.current().expect("current").clone();
        let mut playback = PlaybackState::default();
        playback.begin(entry.id, &entry.track.id, 1);
        playback.stop();
        assert_eq!(queue.current_id(), Some(entry.id));
        assert_eq!(playback.current_entry(), Some(entry.id));
        assert_eq!(playback.play(), PlayAction::Resolve);
        assert_eq!(
            playback.accept_resolve(1, &entry.track.id),
            ResolveDisposition::Stale
        );
    }

    #[test]
    fn end_of_stream_advances_only_once_and_stops_at_the_queue_end() {
        let mut queue = Queue::default();
        queue.replace_search(&[track("aaaaaaaaaaa", "A"), track("bbbbbbbbbbb", "B")], 0);
        let mut playback = PlaybackState::default();
        playback.begin(queue.current_id().expect("current"), "aaaaaaaaaaa", 1);
        assert_eq!(
            playback.accept_resolve(1, "aaaaaaaaaaa"),
            ResolveDisposition::Start
        );
        playback.mark_starting();
        assert!(playback.on_playing());
        assert!(playback.eos_begin());
        assert!(!playback.eos_begin());

        let next = queue.next_index().expect("next");
        let entry = queue.select_index(next).expect("selected");
        playback.begin(entry.id, &entry.track.id, 2);
        assert_eq!(
            playback.accept_resolve(2, "bbbbbbbbbbb"),
            ResolveDisposition::Start
        );
        playback.mark_starting();
        assert!(playback.on_playing());
        assert!(playback.eos_begin());
        assert_eq!(queue.next_index(), None);
        playback.finish();
        assert_eq!(playback.current_entry(), Some(entry.id));
    }
}
