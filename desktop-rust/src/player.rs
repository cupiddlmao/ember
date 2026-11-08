use gstreamer as gst;
use gstreamer::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc;
use std::{cell::Cell, rc::Rc};

static NEXT_PLAYER_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PlayerEventKind {
    Playing,
    Eos,
    Error,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PlayerEvent {
    pub player_id: u64,
    pub kind: PlayerEventKind,
}

/// One playbin and its bus watch for one selected recording.
///
/// Creating a fresh player for each started stream lets the UI reject queued
/// StateChanged/EOS/Error messages by player ID instead of applying them to a
/// newer source on the same reusable pipeline. Child-element state changes are
/// ignored; only the playbin's own Playing state marks the stream ready.
pub struct Player {
    id: u64,
    buffering: Rc<Cell<bool>>,
    playbin: gst::Element,
    _bus_watch: gst::bus::BusWatchGuard,
}

impl Player {
    pub fn check_available() -> Result<(), String> {
        gst::init().map_err(|_| "GStreamer could not be initialized.".to_owned())?;
        for factory in ["playbin", "uridecodebin", "souphttpsrc", "autoaudiosink"] {
            if gst::ElementFactory::find(factory).is_none() {
                return Err(format!(
                    "GStreamer is missing the {factory} plugin. Install the base, good, and soup plugin packages."
                ));
            }
        }
        Ok(())
    }

    pub fn new(events: mpsc::Sender<PlayerEvent>) -> Result<Self, String> {
        Self::check_available()?;
        let playbin = gst::ElementFactory::make("playbin")
            .build()
            .map_err(|_| "GStreamer playbin could not be created.".to_owned())?;
        let id = NEXT_PLAYER_ID.fetch_add(1, Ordering::SeqCst);
        let bus = playbin
            .bus()
            .ok_or_else(|| "GStreamer playback status is unavailable.".to_owned())?;
        let buffering = Rc::new(Cell::new(false));
        let bus_buffering = buffering.clone();
        let source = playbin.clone().upcast::<gst::Object>();
        let _bus_watch = bus
            .add_watch_local(move |_, message| {
                let kind = match message.view() {
                    gst::MessageView::StateChanged(change)
                        if message.src() == Some(&source)
                            && change.current() == gst::State::Playing =>
                    {
                        Some(PlayerEventKind::Playing)
                    }
                    gst::MessageView::Buffering(change) => {
                        bus_buffering.set(change.percent() < 100);
                        None
                    }
                    gst::MessageView::Eos(..) => Some(PlayerEventKind::Eos),
                    gst::MessageView::Error(_) => Some(PlayerEventKind::Error),
                    _ => None,
                };
                if let Some(kind) = kind {
                    let _ = events.send(PlayerEvent {
                        player_id: id,
                        kind,
                    });
                }
                gst::glib::ControlFlow::Continue
            })
            .map_err(|_| "GStreamer playback status monitoring could not start.".to_owned())?;
        Ok(Self {
            id,
            buffering,
            playbin,
            _bus_watch,
        })
    }

    pub fn id(&self) -> u64 {
        self.id
    }

    pub fn set_volume(&self, volume: f64) {
        self.playbin.set_property("volume", volume.clamp(0.0, 1.0));
    }

    /// Sets the temporary stream URI and starts playback. The URI is never
    /// retained by this struct or shown in an error.
    pub fn play_uri(&self, uri: &str) -> Result<(), String> {
        self.playbin.set_property("uri", uri);
        self.playbin
            .set_state(gst::State::Playing)
            .map(|_| ())
            .map_err(|_| {
                "Playback could not start. Check the GStreamer plugins and try another recording."
                    .to_owned()
            })
    }

    pub fn play(&self) -> Result<(), String> {
        self.playbin
            .set_state(gst::State::Playing)
            .map(|_| ())
            .map_err(|_| "Playback could not resume. Check the GStreamer setup.".to_owned())
    }

    pub fn pause(&self) -> Result<(), String> {
        self.playbin
            .set_state(gst::State::Paused)
            .map(|_| ())
            .map_err(|_| "Playback could not be paused.".to_owned())
    }

    pub fn stop(&self) -> Result<(), String> {
        self.playbin
            .set_state(gst::State::Null)
            .map(|_| ())
            .map_err(|_| "Playback could not be stopped cleanly.".to_owned())
    }

    pub fn actually_playing(&self) -> bool {
        !self.buffering.get() && self.playbin.current_state() == gst::State::Playing
    }

    pub fn position_ms(&self) -> Option<u64> {
        self.playbin
            .query_position::<gst::ClockTime>()
            .map(|position| position.mseconds())
    }

    pub fn duration_ms(&self) -> Option<u64> {
        self.playbin
            .query_duration::<gst::ClockTime>()
            .map(|duration| duration.mseconds())
    }

    pub fn seek_to_ms(&self, position_ms: u64) -> Result<(), String> {
        self.playbin
            .seek_simple(
                gst::SeekFlags::FLUSH | gst::SeekFlags::ACCURATE,
                gst::ClockTime::from_nseconds(position_ms.saturating_mul(1_000_000)),
            )
            .map(|_| ())
            .map_err(|_| "This recording could not seek to that timestamp.".to_owned())
    }
}

impl Drop for Player {
    fn drop(&mut self) {
        let _ = self.playbin.set_state(gst::State::Null);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use gtk4::gio::prelude::FileExt;

    #[test]
    fn webm_audio_seeks_to_requested_time_without_resuming_paused_playback() {
        let context = gst::glib::MainContext::new();
        let _guard = context.acquire().expect("context");
        context
            .with_thread_default(|| {
                gst::init().expect("GStreamer");
                let temp = tempfile::tempdir().expect("tempdir");
                let path = temp.path().join("seek.webm");
                let encoded = std::process::Command::new("ffmpeg")
                    .args([
                        "-v",
                        "error",
                        "-f",
                        "lavfi",
                        "-i",
                        "sine=frequency=440:sample_rate=48000",
                        "-t",
                        "12",
                        "-c:a",
                        "libopus",
                        "-y",
                    ])
                    .arg(&path)
                    .status()
                    .expect("ffmpeg fixture encoder");
                assert!(encoded.success());
                let (tx, _) = mpsc::channel();
                let player = Player::new(tx).expect("player");
                let sink = gst::ElementFactory::make("fakesink")
                    .property("sync", true)
                    .build()
                    .expect("sink");
                player.playbin.set_property("audio-sink", &sink);
                player
                    .playbin
                    .set_property("uri", gtk4::gio::File::for_path(&path).uri().as_str());
                player.pause().expect("preroll");
                player
                    .playbin
                    .state(gst::ClockTime::from_seconds(5))
                    .0
                    .expect("ready");
                for target in [5000_u64, 8000, 2000] {
                    player.seek_to_ms(target).expect("seek");
                    player
                        .playbin
                        .state(gst::ClockTime::from_seconds(5))
                        .0
                        .expect("seek complete");
                    let actual = player.position_ms().expect("position");
                    assert!(
                        actual.abs_diff(target) <= 50,
                        "seek target {target} landed at {actual}"
                    );
                    assert_eq!(player.playbin.current_state(), gst::State::Paused);
                }
            })
            .expect("thread context");
    }

    #[test]
    fn child_errors_reach_the_player_event_channel() {
        let context = gst::glib::MainContext::new();
        let _guard = context.acquire().expect("main context");
        context
            .with_thread_default(|| {
                let (tx, rx) = mpsc::channel();
                let player = Player::new(tx).expect("player");
                let child = gst::ElementFactory::make("fakesrc")
                    .build()
                    .expect("source");
                let error =
                    gst::message::Error::builder(gst::ResourceError::NotFound, "fake source error")
                        .src(&child)
                        .build();
                player
                    .playbin
                    .bus()
                    .expect("bus")
                    .post(error)
                    .expect("post");
                for _ in 0..20 {
                    context.iteration(false);
                    if let Ok(event) = rx.try_recv() {
                        assert_eq!(event.player_id, player.id());
                        assert_eq!(event.kind, PlayerEventKind::Error);
                        return;
                    }
                }
                panic!("child error was dropped");
            })
            .expect("thread default");
    }
}
