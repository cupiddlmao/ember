//! MPRIS media controls for the desktop shell.
//!
//! Plasma, GNOME Shell, playerctl, and media keys discover players through
//! `org.mpris.MediaPlayer2` on the session bus. The GTK process owns the UI and
//! GStreamer state, so this module runs the D-Bus service on its own thread and
//! exchanges small command/update messages with the main loop.

use mpris_server::{Metadata, PlaybackStatus, Player, Time, TrackId};
use std::sync::mpsc;
use std::thread;
use tokio::sync::mpsc as async_mpsc;

const BUS_SUFFIX: &str = "io.ember.RustPreview";
const IDENTITY: &str = "Ember";
const DESKTOP_ENTRY: &str = "io.ember.Player";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Playback {
    Playing,
    Paused,
    Stopped,
}

impl From<Playback> for PlaybackStatus {
    fn from(value: Playback) -> Self {
        match value {
            Playback::Playing => Self::Playing,
            Playback::Paused => Self::Paused,
            Playback::Stopped => Self::Stopped,
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TrackInfo {
    pub id: String,
    pub title: String,
    pub artists: Vec<String>,
    pub album: String,
    pub duration_ms: u64,
    pub artwork_url: Option<String>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct State {
    pub track: Option<TrackInfo>,
    pub playback: Playback,
    pub position_ms: u64,
    pub volume: f64,
    pub can_play: bool,
    pub can_pause: bool,
    pub can_go_next: bool,
    pub can_go_previous: bool,
    pub can_seek: bool,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Command {
    Play,
    Pause,
    PlayPause,
    Stop,
    Next,
    Previous,
    SeekBy(i64),
    SetPosition(u64),
    SetVolume(f64),
    Raise,
    Quit,
}

enum Update {
    State(State),
    Position(u64),
    Seeked(u64),
    Volume(f64),
}

/// A small, clone-free handle to the MPRIS service thread.
pub struct Mpris {
    updates: async_mpsc::UnboundedSender<Update>,
}

impl Mpris {
    pub fn start(commands: mpsc::Sender<Command>) -> Self {
        let (updates, updates_rx) = async_mpsc::unbounded_channel();
        let spawned = thread::Builder::new()
            .name("ember-mpris".to_owned())
            .spawn(move || {
                let runtime = match tokio::runtime::Builder::new_current_thread()
                    .enable_all()
                    .build()
                {
                    Ok(runtime) => runtime,
                    Err(error) => {
                        eprintln!("Ember: could not start the MPRIS runtime: {error}");
                        return;
                    }
                };
                if let Err(error) = runtime.block_on(serve(updates_rx, commands)) {
                    // A missing session bus or a stale player name should not
                    // prevent the audio client from working normally.
                    eprintln!("Ember: MPRIS media controls unavailable: {error}");
                }
            });
        if let Err(error) = spawned {
            eprintln!("Ember: could not start MPRIS media controls: {error}");
        }
        Self { updates }
    }

    pub fn update(&self, state: State) {
        let _ = self.updates.send(Update::State(state));
    }

    pub fn position(&self, position_ms: u64) {
        let _ = self.updates.send(Update::Position(position_ms));
    }

    pub fn seeked(&self, position_ms: u64) {
        let _ = self.updates.send(Update::Seeked(position_ms));
    }

    pub fn volume(&self, volume: f64) {
        let _ = self.updates.send(Update::Volume(volume));
    }
}

async fn serve(
    mut updates: async_mpsc::UnboundedReceiver<Update>,
    commands: mpsc::Sender<Command>,
) -> mpris_server::zbus::Result<()> {
    let player = Player::builder(BUS_SUFFIX)
        .identity(IDENTITY)
        .desktop_entry(DESKTOP_ENTRY)
        .can_play(true)
        .can_pause(true)
        .can_go_next(true)
        .can_go_previous(true)
        .can_seek(true)
        .can_raise(true)
        .can_quit(true)
        .build()
        .await?;
    wire_commands(&player, &commands);

    let run = player.run();
    tokio::pin!(run);
    let mut current_track = None;

    loop {
        tokio::select! {
            _ = &mut run => break,
            update = updates.recv() => {
                let Some(update) = update else {
                    break;
                };
                apply(&player, update, &mut current_track).await?;
            }
        }
    }
    Ok(())
}

fn wire_commands(player: &Player, commands: &mpsc::Sender<Command>) {
    let tx = commands.clone();
    player.connect_play(move |_| {
        let _ = tx.send(Command::Play);
    });
    let tx = commands.clone();
    player.connect_pause(move |_| {
        let _ = tx.send(Command::Pause);
    });
    let tx = commands.clone();
    player.connect_play_pause(move |_| {
        let _ = tx.send(Command::PlayPause);
    });
    let tx = commands.clone();
    player.connect_stop(move |_| {
        let _ = tx.send(Command::Stop);
    });
    let tx = commands.clone();
    player.connect_next(move |_| {
        let _ = tx.send(Command::Next);
    });
    let tx = commands.clone();
    player.connect_previous(move |_| {
        let _ = tx.send(Command::Previous);
    });
    let tx = commands.clone();
    player.connect_seek(move |_, offset| {
        let _ = tx.send(Command::SeekBy(offset.as_micros()));
    });
    let tx = commands.clone();
    player.connect_set_position(move |_, _track_id, position| {
        let _ = tx.send(Command::SetPosition(position.as_millis().max(0) as u64));
    });
    let tx = commands.clone();
    player.connect_set_volume(move |_, volume| {
        let _ = tx.send(Command::SetVolume(volume));
    });
    let tx = commands.clone();
    player.connect_raise(move |_| {
        let _ = tx.send(Command::Raise);
    });
    let tx = commands.clone();
    player.connect_quit(move |_| {
        let _ = tx.send(Command::Quit);
    });
}

async fn apply(
    player: &Player,
    update: Update,
    current_track: &mut Option<TrackInfo>,
) -> mpris_server::zbus::Result<()> {
    match update {
        Update::State(state) => {
            if state.track != *current_track {
                let metadata = state
                    .track
                    .as_ref()
                    .map(metadata_for)
                    .unwrap_or_else(empty_metadata);
                player.set_metadata(metadata).await?;
                *current_track = state.track;
            }
            player.set_playback_status(state.playback.into()).await?;
            player.set_can_play(state.can_play).await?;
            player.set_can_pause(state.can_pause).await?;
            player.set_can_go_next(state.can_go_next).await?;
            player.set_can_go_previous(state.can_go_previous).await?;
            player.set_can_seek(state.can_seek).await?;
            player.set_volume(state.volume.clamp(0.0, 1.0)).await?;
            player.set_position(Time::from_millis(ms_to_i64(state.position_ms)));
        }
        Update::Position(position_ms) => {
            player.set_position(Time::from_millis(ms_to_i64(position_ms)));
        }
        Update::Seeked(position_ms) => {
            let position = Time::from_millis(ms_to_i64(position_ms));
            player.set_position(position);
            player.seeked(position).await?;
        }
        Update::Volume(volume) => {
            player.set_volume(volume.clamp(0.0, 1.0)).await?;
        }
    }
    Ok(())
}

fn metadata_for(track: &TrackInfo) -> Metadata {
    let mut builder = Metadata::builder()
        .trackid(track_id(track))
        .length(Time::from_millis(ms_to_i64(track.duration_ms)))
        .title(track.title.clone());
    if !track.artists.is_empty() {
        builder = builder.artist(track.artists.clone());
    }
    if !track.album.trim().is_empty() {
        builder = builder.album(track.album.clone());
    }
    if let Some(artwork_url) = track.artwork_url.as_ref().filter(|url| !url.is_empty()) {
        builder = builder.art_url(artwork_url.clone());
    }
    builder.build()
}

fn empty_metadata() -> Metadata {
    Metadata::builder().trackid(TrackId::NO_TRACK).build()
}

fn track_id(track: &TrackInfo) -> TrackId {
    let safe_id: String = track
        .id
        .chars()
        .map(|character| {
            if character.is_ascii_alphanumeric() || character == '_' {
                character
            } else {
                '_'
            }
        })
        .collect();
    let safe_id = if safe_id.is_empty() {
        "unknown".to_owned()
    } else {
        safe_id
    };
    TrackId::try_from(format!("/io/ember/track/{safe_id}")).unwrap_or(TrackId::NO_TRACK)
}

fn ms_to_i64(milliseconds: u64) -> i64 {
    milliseconds.min(i64::MAX as u64) as i64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(id: &str) -> TrackInfo {
        TrackInfo {
            id: id.to_owned(),
            title: "Title".to_owned(),
            artists: vec!["Artist".to_owned()],
            album: "Album".to_owned(),
            duration_ms: 180_000,
            artwork_url: None,
        }
    }

    #[test]
    fn track_ids_are_safe_dbus_paths() {
        assert_eq!(
            track_id(&track("dm4tkSNKfFI")).as_str(),
            "/io/ember/track/dm4tkSNKfFI"
        );
        assert_eq!(
            track_id(&track("dm4t-SNK_fF")).as_str(),
            "/io/ember/track/dm4t_SNK_fF"
        );
        assert_eq!(track_id(&track("")).as_str(), "/io/ember/track/unknown");
    }

    #[test]
    fn metadata_keeps_the_current_track_identity() {
        let metadata = metadata_for(&track("dm4tkSNKfFI"));
        assert_eq!(
            metadata.trackid().expect("track id").as_str(),
            "/io/ember/track/dm4tkSNKfFI"
        );
        assert_eq!(metadata.title(), Some("Title"));
        assert_eq!(metadata.artist(), Some(vec!["Artist".to_owned()]));
    }
}
