use crate::bridge::{BridgePayload, BridgeService, JobResult};
use crate::model::Track;
use gtk4::glib;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use soup::prelude::*;
use std::fs;
use std::fs::File;
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

const STATUS_INTERVAL_SECS: u64 = 60;
const NOW_INTERVAL_SECS: u64 = 60;
const RETRY_INTERVAL_SECS: u64 = 30;
const MAX_PENDING: usize = 1000;
const MAX_OUTBOX_BYTES: u64 = 1024 * 1024;

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
    #[serde(default)]
    awaiting_finish: bool,
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
    last_entry: Option<crate::queue::EntryId>,
    inflight: Option<(Inflight, u64)>,
    connect_url: Option<String>,
    control_error: Option<String>,
    message: Option<String>,
    awaiting_finish: bool,
    outbox: PathBuf,
}

impl LastFmReporter {
    pub fn load() -> Self {
        Self::from_outbox(outbox_path())
    }

    fn from_outbox(outbox: PathBuf) -> Self {
        let mut value = read_outbox(&outbox);
        let excess = value.pending.len().saturating_sub(MAX_PENDING);
        value.pending.drain(..excess);
        let mut reporter = Self {
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
            last_entry: None,
            inflight: None,
            connect_url: None,
            control_error: None,
            message: None,
            awaiting_finish: value.awaiting_finish,
            outbox,
        };
        if reporter.account.is_empty() && !reporter.pending.is_empty() {
            reporter.pending.clear();
            reporter.save();
        }
        reporter
    }

    pub fn status_text(&self) -> String {
        if let Some(error) = &self.control_error {
            return error.clone();
        }
        if self.awaiting_finish {
            return "Approve Ember in your browser, then click Finish browser authorization."
                .to_owned();
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

    /// Persisted, so a browser approval survives an app restart.
    pub fn authorization_pending(&self) -> bool {
        self.awaiting_finish
    }

    pub fn busy(&self) -> bool {
        self.inflight.is_some()
    }

    pub fn take_connect_url(&mut self) -> Option<String> {
        self.connect_url.take()
    }

    fn replace_account(&mut self, account: String) {
        // A different backend identity must not inherit this client's queue.
        if self.account != account {
            self.pending.clear();
            self.clock = ListeningClock::default();
        }
        self.account = account;
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
        entry_id: Option<crate::queue::EntryId>,
        position_ms: u64,
        duration_ms: u64,
        playing: bool,
        bridge: &BridgeService,
    ) {
        let now = Instant::now();
        let track_id = track.map(|track| track.id.clone());
        if track_id != self.last_track || entry_id != self.last_entry {
            self.last_track = track_id;
            self.last_entry = entry_id;
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

    /// A seek or other position jump must not be counted as listening time.
    pub fn break_continuity(&mut self) {
        self.clock.break_continuity();
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
                    self.replace_account(account);
                    self.connected = connected;
                    if connected {
                        self.awaiting_finish = false;
                    }
                    let known_account = !self.account.is_empty();
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
                if let Err(error) = job.result
                    && error.code() == "lastfm_disconnected" {
                        self.connected = false;
                        self.reauth = true;
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
                        self.awaiting_finish = true;
                        self.save();
                    } else {
                        self.control_error =
                            Some("Last.fm did not return an authorization link.".to_owned());
                    }
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("Last.fm returned an answer Ember couldn't use. Try again.".to_owned())
                }
            },
            Inflight::Finish => match job.result {
                Ok(BridgePayload::LastFm(value)) => {
                    let Some(account) = value
                        .get("username")
                        .and_then(Value::as_str)
                        .filter(|name| !name.is_empty())
                    else {
                        self.control_error =
                            Some("Last.fm did not return an account name.".to_owned());
                        return;
                    };
                    self.replace_account(account.to_owned());
                    self.connected = true;
                    self.configured = true;
                    self.reauth = false;
                    self.message = None;
                    self.awaiting_finish = false;
                    self.last_status = Some(Instant::now());
                    self.save();
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("Last.fm returned an answer Ember couldn't use. Try again.".to_owned())
                }
            },
            Inflight::Disconnect => match job.result {
                Ok(BridgePayload::LastFm(_)) => {
                    self.connected = false;
                    self.reauth = false;
                    self.message = None;
                    self.awaiting_finish = false;
                    self.account.clear();
                    self.pending.clear();
                    self.last_status = Some(Instant::now());
                    self.save();
                }
                Err(error) => self.control_error = Some(error.message().to_owned()),
                _ => {
                    self.control_error =
                        Some("Last.fm returned an answer Ember couldn't use. Try again.".to_owned())
                }
            },
        }
    }

    fn submit(&mut self, kind: Inflight, command: Value, bridge: &BridgeService, now: Instant) {
        match bridge.submit_lastfm(command) {
            Ok(seq) => {
                if kind == Inflight::Status {
                    self.last_status = Some(now);
                }
                self.inflight = Some((kind, seq));
            }
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
        // Storage failures must not interrupt playback; callers keep in-memory state.
        let _ = self.persist_outbox();
    }

    fn persist_outbox(&self) -> io::Result<()> {
        let Some(parent) = self.outbox.parent() else {
            return Ok(());
        };
        fs::create_dir_all(parent)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
        }
        let value = Outbox {
            account: self.account.clone(),
            pending: self.pending.clone(),
            awaiting_finish: self.awaiting_finish,
        };
        let data = serde_json::to_vec(&value).map_err(io::Error::other)?;
        let temp = parent.join(format!("lastfm-outbox-{}.tmp", glib::uuid_string_random()));
        let result = (|| -> io::Result<()> {
            let mut options = fs::OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options.open(&temp)?;
            file.write_all(&data)?;
            file.sync_all()?;
            // The temporary file already has the destination permissions, so the
            // rename below moves both the bytes and their protection.
            fs::rename(&temp, &self.outbox)?;
            File::open(parent)?.sync_all()
        })();
        let _ = fs::remove_file(&temp);
        result
    }
}

fn read_outbox(path: &Path) -> Outbox {
    let metadata = match fs::metadata(path) {
        Ok(metadata) => metadata,
        Err(_) => return Outbox::default(),
    };
    if !metadata.is_file() {
        return Outbox::default();
    }
    if metadata.len() > MAX_OUTBOX_BYTES {
        quarantine_outbox(path);
        return Outbox::default();
    }
    let stored = fs::read(path)
        .ok()
        .and_then(|data| serde_json::from_slice::<Outbox>(&data).ok());
    match stored {
        Some(value) => value,
        None => {
            quarantine_outbox(path);
            Outbox::default()
        }
    }
}

fn quarantine_outbox(path: &Path) {
    let backup = path.with_extension(format!("json.corrupt-{}", glib::uuid_string_random()));
    let _ = fs::rename(path, &backup);
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

/// Song radio is a read-only backend query, so it talks to the server directly
/// instead of going through the music bridge's reporting outbox.
fn connection() -> Result<(String, String), String> {
    let (server, key) = crate::library::connection_fields();
    let uri = glib::Uri::parse(&server, glib::UriFlags::NONE)
        .map_err(|_| "Save your Ember server connection first.")?;
    let local = matches!(
        uri.host().as_deref(),
        Some("localhost" | "127.0.0.1" | "::1")
    );
    if key.is_empty()
        || uri.userinfo().is_some()
        || uri.query().is_some()
        || uri.fragment().is_some()
        || !(uri.scheme() == "https" || (uri.scheme() == "http" && local))
    {
        return Err("Use HTTPS for the Ember server connection.".into());
    }
    Ok((server.trim_end_matches('/').to_owned(), key))
}

async fn request(server: &str, key: &str, body: &Value) -> Result<Value, String> {
    let session = soup::Session::new();
    let radio = body["action"].as_str() == Some("radio");
    session.set_timeout(if radio { 45 } else { 15 });
    let message = soup::Message::new("POST", &format!("{server}/v1/lastfm"))
        .map_err(|_| "Invalid server address.")?;
    message.set_flags(soup::MessageFlags::NO_REDIRECT);
    message
        .request_headers()
        .ok_or("Invalid request.")?
        .append("Authorization", &format!("Bearer {key}"));
    let bytes = glib::Bytes::from_owned(serde_json::to_vec(body).map_err(|_| "Invalid request.")?);
    message.set_request_body_from_bytes(Some("application/json"), Some(&bytes));
    let stream = session
        .send_future(&message, glib::Priority::DEFAULT)
        .await
        .map_err(|_| "Ember server is unavailable.")?;
    let mut data = Vec::new();
    loop {
        let chunk = stream
            .read_bytes_future(4096, glib::Priority::DEFAULT)
            .await
            .map_err(|_| "Could not read server response.")?;
        if chunk.is_empty() {
            break;
        }
        data.extend_from_slice(chunk.as_ref());
        if data.len() > if radio { 65536 } else { 16384 } {
            return Err("Server response is too large.".into());
        }
    }
    let value: Value = serde_json::from_slice(&data).map_err(|_| "Invalid server response.")?;
    if message.status() != soup::Status::Ok {
        return Err(format!(
            "{}: {}",
            if message.status() == soup::Status::BadRequest {
                "HTTP 400"
            } else {
                "Last.fm"
            },
            value["error"]["message"]
                .as_str()
                .unwrap_or("Last.fm request failed.")
                .chars()
                .take(300)
                .collect::<String>()
        ));
    }
    Ok(value)
}

pub async fn song_radio(seed: &Track) -> Result<Vec<Track>, String> {
    let (server, key) = connection()?;
    let value = request(
        &server,
        &key,
        &json!({
            "action": "radio", "title": seed.name, "artist": seed.artist_line()
        }),
    )
    .await?;
    radio_mix(value, seed)
}

fn radio_mix(value: Value, seed: &Track) -> Result<Vec<Track>, String> {
    let rows = value
        .get("tracks")
        .and_then(Value::as_array)
        .filter(|rows| rows.len() <= 12)
        .ok_or("Invalid radio response.")?;
    let mut tracks = vec![seed.clone()];
    for row in rows {
        let track: crate::library::LibraryTrack =
            serde_json::from_value(row.clone()).map_err(|_| "Invalid radio recording.")?;
        let track = track.playable()?;
        if !tracks.iter().any(|existing| existing.id == track.id) {
            tracks.push(track);
        }
    }
    if tracks.len() == 1 {
        return Err("No matching Last.fm recommendations.".into());
    }
    Ok(tracks)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::bridge::JobKind;
    use crate::config::BridgeConfig;
    use std::os::unix::fs::PermissionsExt;
    use std::sync::mpsc;
    use std::time::Duration;

    #[test]
    fn radio_keeps_seed_first_deduplicates_and_rejects_bad_responses() {
        let seed = Track {
            id: "aaaaaaaaaaa".into(),
            uri: "https://music.youtube.com/watch?v=aaaaaaaaaaa".into(),
            name: "Seed".into(),
            artists: vec!["Artist".into()],
            album: "Album".into(),
            duration_ms: 180000,
            artwork_url: None,
        };
        let row =
            json!({"id":"bbbbbbbbbbb","title":"Related","artist":"Artist","durationMs":180000});
        let seed_row =
            serde_json::to_value(crate::library::LibraryTrack::from_track(&seed)).unwrap();
        let tracks = radio_mix(
            json!({"tracks":[row.clone(), seed_row.clone(), row.clone()]}),
            &seed,
        )
        .unwrap();
        assert_eq!(
            tracks.iter().map(|t| t.id.as_str()).collect::<Vec<_>>(),
            ["aaaaaaaaaaa", "bbbbbbbbbbb"]
        );
        for value in [
            json!({"tracks":[]}),
            json!({"tracks":[seed_row]}),
            json!({"tracks":[{"id":"bad"}]}),
            json!({"tracks":vec![row;13]}),
            json!({}),
        ] {
            assert!(radio_mix(value, &seed).is_err());
        }
    }

    const STUB_BRIDGE: &str = r#"#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
request=$(cat)
printf '%s\n' "$request" >> "$ROOT/requests.log"
action=$(printf '%s' "$request" | sed -n 's/.*"action":"\([^"]*\)".*/\1/p')
operation=$(printf '%s' "$request" | sed -n 's/.*"operation":"\([^"]*\)".*/\1/p')
case "$action:$operation" in
    lastfm:status)
        count=0
        [ -f "$ROOT/status.count" ] && count=$(cat "$ROOT/status.count")
        count=$((count + 1))
        printf '%s' "$count" > "$ROOT/status.count"
        if [ "$count" -eq 1 ]; then
            printf '%s\n' '{"ok":true,"data":{"configured":true,"connected":false,"username":"","pending":0}}'
        else
            printf '%s\n' '{"ok":true,"data":{"configured":true,"connected":false,"username":"desktop-tester","pending":0}}'
        fi
        ;;
    lastfm:connect)
        printf '%s\n' '{"ok":true,"data":{"url":"https://www.last.fm/api/auth/?api_key=stub&token=stub-token"}}'
        ;;
    lastfm:finish)
        printf '%s\n' '{"ok":true,"data":{"ok":true,"username":"desktop-tester"}}'
        ;;
    lastfm:disconnect)
        printf '%s\n' '{"ok":true,"data":{"ok":true}}'
        ;;
    *)
        printf '%s\n' '{"ok":false,"error":{"code":"bridge","message":"Unknown stub request."}}'
        ;;
esac
"#;

    const ACCOUNT_STUB_BRIDGE: &str = r#"#!/bin/sh
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
request=$(cat)
printf '%s\n' "$request" >> "$ROOT/requests.log"
action=$(printf '%s' "$request" | sed -n 's/.*"action":"\([^"]*\)".*/\1/p')
operation=$(printf '%s' "$request" | sed -n 's/.*"operation":"\([^"]*\)".*/\1/p')
finish_username=desktop-tester
[ -f "$ROOT/finish-username" ] && finish_username=$(cat "$ROOT/finish-username")
case "$action:$operation" in
    lastfm:status)
        connected=$(cat "$ROOT/backend-connected")
        configured=$(cat "$ROOT/backend-configured")
        username=$(cat "$ROOT/backend-username")
        printf '{"ok":true,"data":{"configured":%s,"connected":%s,"username":"%s","pending":0}}\n' "$configured" "$connected" "$username"
        ;;
    lastfm:connect)
        printf '%s\n' '{"ok":true,"data":{"url":"https://www.last.fm/api/auth/?api_key=stub&token=stub-token"}}'
        ;;
    lastfm:finish)
        printf true > "$ROOT/backend-connected"
        printf true > "$ROOT/backend-configured"
        printf '%s' "$finish_username" > "$ROOT/backend-username"
        printf '{"ok":true,"data":{"ok":true,"username":"%s"}}\n' "$finish_username"
        ;;
    lastfm:disconnect)
        printf false > "$ROOT/backend-connected"
        printf true > "$ROOT/backend-configured"
        : > "$ROOT/backend-username"
        printf '%s\n' '{"ok":true,"data":{"ok":true}}'
        ;;
    *)
        printf '%s\n' '{"ok":false,"error":{"code":"bridge","message":"Unknown stub request."}}'
        ;;
esac
"#;

    fn fake_checkout(script: &str) -> tempfile::TempDir {
        let root = tempfile::tempdir().expect("temp checkout");
        fs::write(root.path().join("music.py"), "# stub\n").expect("music.py");
        let bridge_dir = root.path().join("desktop-rust");
        fs::create_dir_all(&bridge_dir).expect("bridge dir");
        fs::write(bridge_dir.join("bridge.py"), "# stub\n").expect("bridge.py");
        let venv = root.path().join(".venv/bin");
        fs::create_dir_all(&venv).expect("venv dir");
        let python = venv.join("python");
        fs::write(&python, script).expect("stub bridge");
        fs::set_permissions(&python, fs::Permissions::from_mode(0o755)).expect("executable");
        root
    }

    fn stub_checkout() -> tempfile::TempDir {
        fake_checkout(STUB_BRIDGE)
    }

    fn queued_listen(id: &str) -> LastFmEvent {
        LastFmEvent {
            id: id.to_owned(),
            title: "Song".to_owned(),
            artist: "Artist".to_owned(),
            album: String::new(),
            duration: 180,
            listened: 90,
            started: 1,
        }
    }

    fn seed_outbox(outbox: &std::path::Path, account: &str, ids: &[&str]) {
        let seeded = Outbox {
            account: account.to_owned(),
            pending: ids.iter().map(|id| queued_listen(id)).collect(),
            awaiting_finish: false,
        };
        fs::create_dir_all(outbox.parent().expect("outbox parent")).expect("state dir");
        fs::write(outbox, serde_json::to_vec(&seeded).expect("encode outbox"))
            .expect("seed outbox");
    }

    fn saved_outbox(outbox: &std::path::Path) -> Outbox {
        serde_json::from_slice(&fs::read(outbox).expect("saved outbox")).expect("decode outbox")
    }

    fn set_backend_state(
        root: &std::path::Path,
        connected: bool,
        configured: bool,
        username: &str,
    ) {
        fs::write(
            root.join("backend-connected"),
            if connected { "true" } else { "false" },
        )
        .expect("backend connected");
        fs::write(
            root.join("backend-configured"),
            if configured { "true" } else { "false" },
        )
        .expect("backend configured");
        fs::write(root.join("backend-username"), username).expect("backend username");
    }

    fn account_stub_checkout() -> tempfile::TempDir {
        let root = fake_checkout(ACCOUNT_STUB_BRIDGE);
        set_backend_state(root.path(), false, true, "");
        root
    }

    fn stub_operations(root: &std::path::Path) -> Vec<String> {
        let log = fs::read_to_string(root.join("requests.log")).expect("stub request log");
        log.lines()
            .map(|line| {
                let request: Value = serde_json::from_str(line).expect("stub request JSON");
                assert_eq!(
                    request.get("action").and_then(Value::as_str),
                    Some("lastfm")
                );
                request
                    .get("command")
                    .and_then(|command| command.get("operation"))
                    .and_then(Value::as_str)
                    .expect("operation")
                    .to_owned()
            })
            .collect()
    }

    fn next_result(rx: &mpsc::Receiver<JobResult>) -> JobResult {
        let job = rx
            .recv_timeout(Duration::from_secs(5))
            .expect("stub bridge result");
        assert_eq!(job.kind, JobKind::LastFm);
        job
    }

    #[test]
    fn settings_control_flow_uses_the_lastfm_stub_backend() {
        let checkout = stub_checkout();
        let outbox = checkout.path().join("state/lastfm-outbox.json");
        seed_outbox(&outbox, "", &[]);

        let (tx, rx) = mpsc::channel();
        let bridge = BridgeService::new(
            BridgeConfig::from_root(checkout.path()).expect("stub config"),
            tx,
        )
        .expect("stub bridge service");
        let mut reporter = LastFmReporter::from_outbox(outbox.clone());

        reporter.sample(None, None, 0, 0, false, &bridge);
        reporter.handle_result(next_result(&rx));
        assert!(!reporter.connected());
        assert!(!reporter.reauth);
        assert!(!reporter.authorization_pending());

        reporter
            .control("connect", &bridge)
            .expect("connect starts");
        reporter.handle_result(next_result(&rx));
        assert!(reporter.authorization_pending());
        assert_eq!(
            reporter.take_connect_url().as_deref(),
            Some("https://www.last.fm/api/auth/?api_key=stub&token=stub-token")
        );
        assert_eq!(
            reporter.status_text(),
            "Approve Ember in your browser, then click Finish browser authorization."
        );

        reporter.control("finish", &bridge).expect("finish starts");
        reporter.handle_result(next_result(&rx));
        assert!(reporter.connected());
        assert!(!reporter.authorization_pending());
        assert_eq!(reporter.account, "desktop-tester");
        assert_eq!(
            reporter.status_text(),
            "Last.fm connected as desktop-tester."
        );

        reporter
            .control("disconnect", &bridge)
            .expect("disconnect starts");
        reporter.handle_result(next_result(&rx));
        assert!(!reporter.connected());
        assert!(!reporter.reauth);
        assert!(!reporter.authorization_pending());
        assert!(reporter.account.is_empty());
        let saved: Outbox =
            serde_json::from_slice(&fs::read(&outbox).expect("saved outbox after disconnect"))
                .expect("decode outbox");
        assert!(saved.account.is_empty());
        assert!(saved.pending.is_empty());

        reporter.last_status = None;
        reporter.sample(None, None, 0, 0, false, &bridge);
        reporter.handle_result(next_result(&rx));
        assert!(!reporter.connected());
        assert!(reporter.reauth);
        assert_eq!(
            reporter.status_text(),
            "Last.fm needs reauthorization. Reconnect here or on your iPhone."
        );

        let log =
            fs::read_to_string(checkout.path().join("requests.log")).expect("stub request log");
        let operations = log
            .lines()
            .map(|line| {
                let request: Value = serde_json::from_str(line).expect("stub request JSON");
                assert_eq!(
                    request.get("action").and_then(Value::as_str),
                    Some("lastfm")
                );
                request
                    .get("command")
                    .and_then(|command| command.get("operation"))
                    .and_then(Value::as_str)
                    .expect("operation")
                    .to_owned()
            })
            .collect::<Vec<_>>();
        assert_eq!(
            operations,
            ["status", "connect", "finish", "disconnect", "status"]
        );
    }

    #[test]
    fn account_scope_prevents_cross_account_replay() {
        let checkout = account_stub_checkout();
        let outbox = checkout.path().join("state/lastfm-outbox.json");

        seed_outbox(&outbox, "", &["orphan"]);
        let reporter = LastFmReporter::from_outbox(outbox.clone());
        assert!(reporter.account.is_empty());
        assert!(reporter.pending.is_empty());
        let saved = saved_outbox(&outbox);
        assert!(saved.account.is_empty());
        assert!(saved.pending.is_empty());

        let (tx, rx) = mpsc::channel();
        let bridge = BridgeService::new(
            BridgeConfig::from_root(checkout.path()).expect("stub config"),
            tx,
        )
        .expect("stub bridge service");
        set_backend_state(checkout.path(), true, true, "account-a");
        seed_outbox(&outbox, "account-a", &["a-one"]);
        let mut reporter = LastFmReporter::from_outbox(outbox.clone());
        reporter.sample(None, None, 0, 0, false, &bridge);
        reporter.handle_result(next_result(&rx));
        assert!(reporter.connected());
        assert_eq!(reporter.account, "account-a");
        assert_eq!(reporter.pending.len(), 1);
        assert_eq!(
            reporter.status_text(),
            "Last.fm connected as account-a · 1 pending."
        );

        set_backend_state(checkout.path(), false, true, "");
        reporter.last_status = None;
        reporter.sample(None, None, 0, 0, false, &bridge);
        reporter.handle_result(next_result(&rx));
        assert!(!reporter.connected());
        assert!(!reporter.reauth);
        assert!(!reporter.authorization_pending());
        assert!(reporter.account.is_empty());
        assert!(reporter.pending.is_empty());
        let saved = saved_outbox(&outbox);
        assert!(saved.account.is_empty());
        assert!(saved.pending.is_empty());

        set_backend_state(checkout.path(), true, true, "account-a");
        seed_outbox(&outbox, "account-a", &["a-two"]);
        let mut reporter = LastFmReporter::from_outbox(outbox.clone());
        reporter.sample(None, None, 0, 0, false, &bridge);
        reporter.handle_result(next_result(&rx));
        assert_eq!(reporter.pending.len(), 1);

        fs::write(checkout.path().join("finish-username"), "account-b").expect("finish username");
        reporter
            .control("connect", &bridge)
            .expect("connect starts");
        reporter.handle_result(next_result(&rx));
        assert!(reporter.authorization_pending());
        assert_eq!(reporter.pending.len(), 1);

        reporter.control("finish", &bridge).expect("finish starts");
        reporter.handle_result(next_result(&rx));
        assert!(reporter.connected());
        assert!(!reporter.authorization_pending());
        assert_eq!(reporter.account, "account-b");
        assert!(reporter.pending.is_empty());
        assert_eq!(reporter.status_text(), "Last.fm connected as account-b.");
        let saved = saved_outbox(&outbox);
        assert_eq!(saved.account, "account-b");
        assert!(saved.pending.is_empty());
        assert_eq!(
            stub_operations(checkout.path()),
            ["status", "status", "status", "connect", "finish"]
        );
    }

    #[test]
    fn finish_without_an_account_name_is_rejected() {
        let checkout = stub_checkout();
        let outbox = checkout.path().join("state/lastfm-outbox.json");
        seed_outbox(&outbox, "account-a", &["a-one"]);
        let mut reporter = LastFmReporter::from_outbox(outbox);
        reporter.inflight = Some((Inflight::Finish, 3));
        reporter.handle_result(JobResult {
            kind: JobKind::LastFm,
            seq: 3,
            result: Ok(BridgePayload::LastFm(serde_json::json!({"ok": true}))),
        });
        assert!(!reporter.connected());
        assert_eq!(
            reporter.control_error.as_deref(),
            Some("Last.fm did not return an account name.")
        );
        assert_eq!(reporter.account, "account-a");
        assert_eq!(reporter.pending.len(), 1);
    }

    #[test]
    fn authorization_pending_survives_a_restart() {
        let checkout = stub_checkout();
        let outbox = checkout.path().join("state/lastfm-outbox.json");
        seed_outbox(&outbox, "", &[]);
        let (tx, rx) = mpsc::channel();
        let bridge = BridgeService::new(
            BridgeConfig::from_root(checkout.path()).expect("stub config"),
            tx,
        )
        .expect("stub bridge service");

        let mut reporter = LastFmReporter::from_outbox(outbox.clone());
        reporter.control("connect", &bridge).expect("connect starts");
        reporter.handle_result(next_result(&rx));
        assert!(reporter.authorization_pending());

        // A fresh reporter stands in for a restarted app loading the same outbox.
        let mut restarted = LastFmReporter::from_outbox(outbox.clone());
        assert!(restarted.authorization_pending());
        restarted.control("finish", &bridge).expect("finish starts");
        restarted.handle_result(next_result(&rx));
        assert!(restarted.connected());
        assert!(!restarted.authorization_pending());
        assert_eq!(restarted.account, "desktop-tester");
        assert!(!saved_outbox(&outbox).awaiting_finish);
    }

    fn corrupt_backup(dir: &std::path::Path) -> Option<std::path::PathBuf> {
        fs::read_dir(dir)
            .expect("outbox dir")
            .filter_map(|entry| {
                let path = entry.ok()?.path();
                let backup = path
                    .file_name()
                    .and_then(|name| name.to_str())
                    .is_some_and(|name| name.starts_with("lastfm-outbox.json.corrupt-"));
                backup.then_some(path)
            })
            .next()
    }

    #[test]
    fn outbox_save_replaces_the_file_atomically() {
        let dir = tempfile::tempdir().expect("temp outbox");
        let outbox = dir.path().join("state/lastfm-outbox.json");
        seed_outbox(&outbox, "account-a", &["a-one"]);
        let mut reporter = LastFmReporter::from_outbox(outbox.clone());
        reporter.pending.push(queued_listen("a-two"));
        reporter.save();

        let saved = saved_outbox(&outbox);
        assert_eq!(saved.account, "account-a");
        assert_eq!(saved.pending.len(), 2);
        let mut names: Vec<String> = fs::read_dir(outbox.parent().expect("outbox parent"))
            .expect("outbox dir")
            .filter_map(|entry| {
                entry
                    .ok()
                    .map(|entry| entry.file_name().to_string_lossy().into_owned())
            })
            .collect();
        names.sort();
        assert_eq!(names, ["lastfm-outbox.json".to_owned()]);
    }

    #[test]
    fn corrupt_outbox_is_quarantined_instead_of_loaded() {
        let dir = tempfile::tempdir().expect("temp outbox");
        let outbox = dir.path().join("state/lastfm-outbox.json");
        let original = b"{\"account\":";
        fs::create_dir_all(outbox.parent().expect("outbox parent")).expect("state dir");
        fs::write(&outbox, original).expect("corrupt outbox");

        let reporter = LastFmReporter::from_outbox(outbox.clone());
        assert!(reporter.account.is_empty());
        assert!(reporter.pending.is_empty());
        assert!(!outbox.exists());
        let backup =
            corrupt_backup(outbox.parent().expect("outbox parent")).expect("quarantined outbox");
        assert_eq!(fs::read(&backup).expect("quarantined outbox"), original);
    }

    #[test]
    fn oversized_outbox_is_quarantined_instead_of_loaded() {
        let dir = tempfile::tempdir().expect("temp outbox");
        let outbox = dir.path().join("state/lastfm-outbox.json");
        let padding = "x".repeat(MAX_OUTBOX_BYTES as usize);
        let original =
            format!("{{\"account\":\"account-a\",\"pending\":[],\"padding\":\"{padding}\"}}");
        fs::create_dir_all(outbox.parent().expect("outbox parent")).expect("state dir");
        fs::write(&outbox, original.as_bytes()).expect("oversized outbox");

        let reporter = LastFmReporter::from_outbox(outbox.clone());
        assert!(reporter.account.is_empty());
        assert!(reporter.pending.is_empty());
        assert!(!outbox.exists());
        assert!(
            corrupt_backup(outbox.parent().expect("outbox parent")).is_some(),
            "oversized outbox should be quarantined"
        );
    }

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
            awaiting_finish: true,
        };
        let encoded = serde_json::to_vec(&value).expect("encode");
        let decoded: Outbox = serde_json::from_slice(&encoded).expect("decode");
        assert_eq!(decoded.account, "listener");
        assert_eq!(decoded.pending.len(), 1);
        assert!(decoded.awaiting_finish);
    }
}
