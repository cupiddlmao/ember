use crate::bridge::{BridgePayload, BridgeService, JobResult};
use crate::model::Track;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::fs;
use std::path::PathBuf;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const STATUS_INTERVAL_SECS: u64 = 60;
const NOW_INTERVAL_SECS: u64 = 60;
const RETRY_INTERVAL_SECS: u64 = 30;
const MAX_PENDING: usize = 1000;

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct LastFmEvent {
    id: String,
    title: String,
    artist: String,
    album: String,
    duration: u64,
    listened: u64,
    started: u64,
}

#[derive(Default, Serialize, Deserialize)]
struct Outbox {
    #[serde(default)]
    account: String,
    #[serde(default)]
    pending: Vec<LastFmEvent>,
}

struct ListeningClock {
    listened: f64,
    previous: Option<(f64, Instant, bool)>,
}

impl Default for ListeningClock {
    fn default() -> Self {
        Self {
            listened: 0.0,
            previous: None,
        }
    }
}

impl ListeningClock {
    fn sample(&mut self, position: f64, now: Instant, playing: bool) {
        let previous = self.previous.replace((position, now, playing));
        let Some((previous_position, previous_time, was_playing)) = previous else {
            return;
        };
        if !playing || !was_playing || !position.is_finite() || !previous_position.is_finite() {
            return;
        }
        let wall = now.duration_since(previous_time).as_secs_f64();
        let advance = position - previous_position;
        if wall > 0.0 && wall <= 3.0 && advance > 0.0 && advance <= wall + 0.75 {
            self.listened += wall.min(advance);
        }
    }

    fn break_continuity(&mut self) {
        self.previous = None;
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Inflight {
    Status,
    Now,
    Scrobble,
    Connect,
    Finish,
    Disconnect,
}

pub struct LastFmReporter {
    clock: ListeningClock,
    playback_id: String,
    started: u64,
    submitted: bool,
    last_now: Option<Instant>,
    was_playing: bool,
    last_attempt: Option<Instant>,
    last_status: Option<Instant>,
    pending: Vec<LastFmEvent>,
    connected: bool,
    configured: bool,
    account: String,
    reauth: bool,
    last_track: Option<String>,
    inflight: Option<(Inflight, u64)>,
    connect_url: Option<String>,
    control_error: Option<String>,
    message: Option<String>,
    outbox: PathBuf,
}

impl LastFmReporter {
    pub fn load() -> Self {
        let outbox = outbox_path();
        let mut value = Outbox::default();
        if let Ok(metadata) = fs::metadata(&outbox) {
            if metadata.len() <= 1024 * 1024 {
                if let Ok(data) = fs::read(&outbox) {
                    if let Ok(parsed) = serde_json::from_slice::<Outbox>(&data) {
                        value = parsed;
                    }
                }
            }
        }
        let excess = value.pending.len().saturating_sub(MAX_PENDING);
        value.pending.drain(..excess);
        Self {
            clock: ListeningClock::default(),
            playback_id: uuid_like(),
            started: 0,
            submitted: false,
            last_now: None,
            was_playing: false,
            last_attempt: None,
            last_status: None,
            pending: value.pending,
            connected: false,
            configured: false,
            account: value.account,
            reauth: false,
            last_track: None,
            inflight: None,
            connect_url: None,
            control_error: None,
            message: None,
            outbox,
        }
    }

    pub fn status_text(&self) -> String {
        if let Some(error) = &self.control_error {
            return error.clone();
        }
        if let Some(message) = &self.message {
            return message.clone();
        }
        if self.reauth {
            return "Last.fm needs reauthorization. Reconnect here or on your iPhone.".to_owned();
        }
        if self.connected {
            let pending = if self.pending.is_empty() {
                String::new()
            } else {
                format!(" · {} pending", self.pending.len())
            };
            return format!("Last.fm connected as {}{pending}.", self.account);
        }
        "Last.fm is not connected. Connect it here or on your iPhone.".to_owned()
    }

    pub fn connected(&self) -> bool {
        self.connected
    }

    pub fn configured(&self) -> bool {
        self.configured
    }

    pub fn busy(&self) -> bool {
        self.inflight.is_some()
    }

    pub fn take_connect_url(&mut self) -> Option<String> {
        self.connect_url.take()
    }

    pub fn set_error(&mut self, message: impl Into<String>) {
        self.control_error = Some(message.into());
    }

    pub fn control(&mut self, operation: &str, bridge: &BridgeService) -> Result<(), String> {
        let kind = match operation {
            "connect" => Inflight::Connect,
            "finish" => Inflight::Finish,
            "disconnect" => Inflight::Disconnect,
            _ => return Err("Unknown Last.fm action.".to_owned()),
        };
        if self.busy() {
            return Err("Wait for the current Last.fm request to finish.".to_owned());
        }
        self.control_error = None;
        self.message = None;
        self.submit(
            kind,
            json!({ "operation": operation }),
            bridge,
            Instant::now(),
        );
        if self.inflight.is_some() {
            Ok(())
        } else {
            Err("The Last.fm request could not start.".to_owned())
        }
    }

    pub fn sample(
        &mut self,
        track: Option<&Track>,
        position_ms: u64,
        duration_ms: u64,
        playing: bool,
        bridge: &BridgeService,
    ) {
        let now = Instant::now();
        let track_id = track.map(|track| track.id.clone());
        if track_id != self.last_track {
            self.last_track = track_id;
            self.playback_id = uuid_like();
            self.started = 0;
            self.submitted = false;
            self.last_now = None;
            self.was_playing = false;
            self.clock = ListeningClock::default();
        }

        if !self.busy() && elapsed(self.last_status, now) >= STATUS_INTERVAL_SECS {
            self.submit(
                Inflight::Status,
                json!({"operation": "status"}),
                bridge,
                now,
            );
            return;
        }
        let Some(track) = track else {
            self.clock.break_continuity();
            self.was_playing = playing;
            return;
        };
        if !self.connected {
            self.clock.break_continuity();
            self.was_playing = playing;
            return;
        }

        self.clock.sample(position_ms as f64 / 1000.0, now, playing);
        let mut now_body = None;
        if playing {
            if self.started == 0 {
                self.started = unix_seconds();
            }
            if !self.was_playing || elapsed(self.last_now, now) >= NOW_INTERVAL_SECS {
                now_body = Some(self.command("now", track, None));
                self.last_now = Some(now);
            }
            if !self.submitted
                && duration_ms > 30_000
                && self.clock.listened >= (duration_ms as f64 / 2000.0).min(240.0)
            {
                let event = LastFmEvent {
                    id: self.playback_id.clone(),
                    title: track.name.clone(),
                    artist: track.artist_line(),
                    album: track.album.clone(),
                    duration: duration_ms / 1000,
                    listened: self.clock.listened.ceil() as u64,
                    started: self.started,
                };
                self.pending.push(event);
                if self.pending.len() > MAX_PENDING {
                    self.pending.remove(0);
                }
                self.submitted = true;
                self.save();
            }
        }
        self.was_playing = playing;

        if self.busy() {
            return;
        }
        if let Some(event) = self.pending.first().cloned() {
            if elapsed(self.last_attempt, now) >= RETRY_INTERVAL_SECS {
                self.last_attempt = Some(now);
                self.submit(Inflight::Scrobble, self.event_command(&event), bridge, now);
            }
            return;
        }
        if let Some(body) = now_body {
            self.submit(Inflight::Now, body, bridge, now);
        }
    }

    pub fn handle_result(&mut self, job: JobResult) {
        let Some((kind, seq)) = self.inflight else {
            return;
        };
        if job.seq != seq {
            return;
        }
        self.inflight = None;
        match kind {
            Inflight::Status => {
                if let Ok(BridgePayload::LastFm(value)) = job.result {
                    let connected = value.get("connected").and_then(Value::as_bool) == Some(true);
                    let configured =
                        value.get("configured").and_then(Value::as_bool) != Some(false);
                    let account = value
                        .get("username")
                        .and_then(Value::as_str)
                        .unwrap_or("")
                        .to_owned();
                    self.configured = configured;
                    if !self.account.is_empty() && !account.is_empty() && self.account != account {
                        self.pending.clear();
                        self.clock = ListeningClock::default();
                    }
                    self.connected = connected;
                    let known_account = !account.is_empty();
                    self.account = account;
                    if connected || !configured {
                        self.reauth = false;
                    } else if known_account {
                        // The backend kept the account name after clearing a revoked session.
                        self.reauth = true;
                    }
                    self.save();
                }
            }
            Inflight::Scrobble => match job.result {
                Ok(BridgePayload::LastFm(_)) => self.complete_first(),
                Err(error)
                    if error.code() == "event_conflict" || error.code() == "invalid_listen" =>
                {
                    self.complete_first();
                }
                Err(error) if error.code() == "lastfm_disconnected" => {
                    self.connected = false;
                    self.reauth = true;
                }
                _ => {}
            },
            Inflight::Now => {
                if let Err(error) = job.result {
                    if error.code() == "lastfm_disconnected" {
                        self.connected = false;
                        self.reauth = true;
                    }
                }
            }
            Inflight::Connect => match job.result {
                Ok(BridgePayload::LastFm(value)) => {
                    if let Some(url) = value.get("url").and_then(Value::as_str) {
                        self.connect_url = Some(url.to_owned());
                        self.message = Some(
                            "Approve Ember in your browser, then click Finish browser authorization."
                                .to_owned(),
                        );
                    } else {
                        self.control_error =
                            Some("Last.fm did not return an authorization link.".to_owned());
                    }
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("The Last.fm bridge returned the wrong result type.".to_owned())
                }
            },
            Inflight::Finish => match job.result {
                Ok(BridgePayload::LastFm(value)) => {
                    self.connected = true;
                    self.configured = true;
                    self.reauth = false;
                    self.message = None;
                    if let Some(account) = value.get("username").and_then(Value::as_str) {
                        self.account = account.to_owned();
                    }
                    self.last_status = Some(Instant::now());
                    self.save();
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("The Last.fm bridge returned the wrong result type.".to_owned())
                }
            },
            Inflight::Disconnect => match job.result {
                Ok(BridgePayload::LastFm(_)) => {
                    self.connected = false;
                    self.reauth = false;
                    self.message = None;
                    self.account.clear();
                    self.pending.clear();
                    self.last_status = Some(Instant::now());
                    self.save();
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("The Last.fm bridge returned the wrong result type.".to_owned())
                }
            },
        }
    }

    fn submit(&mut self, kind: Inflight, command: Value, bridge: &BridgeService, now: Instant) {
        match bridge.submit_lastfm(command) {
            Ok(seq) => self.inflight = Some((kind, seq)),
            Err(_) => {
                self.last_status = Some(now);
                self.last_attempt = Some(now);
            }
        }
    }

    fn complete_first(&mut self) {
        if !self.pending.is_empty() {
            self.pending.remove(0);
            self.save();
        }
    }

    fn command(&self, operation: &str, track: &Track, event: Option<&LastFmEvent>) -> Value {
        let mut value = json!({
            "operation": operation,
            "title": track.name,
            "artist": track.artist_line(),
            "album": track.album,
        });
        if let Some(event) = event {
            value["id"] = json!(event.id);
            value["duration"] = json!(event.duration);
            value["listened"] = json!(event.listened);
            value["started"] = json!(event.started);
        }
        value
    }

    fn event_command(&self, event: &LastFmEvent) -> Value {
        json!({
            "operation": "scrobble",
            "id": event.id,
            "title": event.title,
            "artist": event.artist,
            "album": event.album,
            "duration": event.duration,
            "listened": event.listened,
            "started": event.started,
        })
    }

    fn save(&self) {
        let Some(parent) = self.outbox.parent() else {
            return;
        };
        if fs::create_dir_all(parent).is_err() {
            return;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(parent, fs::Permissions::from_mode(0o700));
        }
        let value = Outbox {
            account: self.account.clone(),
            pending: self.pending.clone(),
        };
        let Ok(data) = serde_json::to_vec(&value) else {
            return;
        };
        let _ = fs::write(&self.outbox, data);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&self.outbox, fs::Permissions::from_mode(0o600));
        }
    }
}

fn outbox_path() -> PathBuf {
    let root = std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".local/state")))
        .unwrap_or_else(std::env::temp_dir);
    root.join("ember-player/lastfm-outbox.json")
}

fn elapsed(previous: Option<Instant>, now: Instant) -> u64 {
    previous
        .map(|time| now.duration_since(time).as_secs())
        .unwrap_or(u64::MAX)
}

fn unix_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs())
        .unwrap_or(0)
}

fn uuid_like() -> String {
    format!(
        "{:x}-{:x}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0),
        std::process::id()
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn clock_ignores_pauses_and_seeks() {
        let mut clock = ListeningClock::default();
        let start = Instant::now();
        clock.sample(0.0, start, true);
        clock.sample(1.0, start + std::time::Duration::from_secs(1), true);
        clock.sample(1.0, start + std::time::Duration::from_secs(2), false);
        clock.sample(1.0, start + std::time::Duration::from_secs(102), false);
        clock.sample(100.0, start + std::time::Duration::from_secs(103), true);
        assert!(clock.listened < 2.0);
        clock.sample(101.0, start + std::time::Duration::from_secs(104), true);
        assert!(clock.listened >= 2.0);
    }

    #[test]
    fn outbox_round_trips_events() {
        let event = LastFmEvent {
            id: "one".into(),
            title: "Song".into(),
            artist: "Artist".into(),
            album: String::new(),
            duration: 180,
            listened: 90,
            started: 1,
        };
        let value = Outbox {
            account: "listener".into(),
            pending: vec![event],
        };
        let encoded = serde_json::to_vec(&value).expect("encode");
        let decoded: Outbox = serde_json::from_slice(&encoded).expect("decode");
        assert_eq!(decoded.account, "listener");
        assert_eq!(decoded.pending.len(), 1);
    }
}
