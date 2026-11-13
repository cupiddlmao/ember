use crate::model::Track;
use gtk4::{gio, glib, prelude::*};
use serde_json::{Value, json};
use soup::prelude::*;
use std::{
    cell::RefCell,
    rc::Rc,
    time::{Instant, SystemTime, UNIX_EPOCH},
};

#[derive(Default)]
pub struct ListeningClock {
    pub listened: f64,
    previous: Option<(f64, f64, bool)>,
}
impl ListeningClock {
    pub fn sample(&mut self, position: f64, time: f64, playing: bool) {
        if let Some((old_position, old_time, old_playing)) = self.previous {
            let wall = time - old_time;
            let advance = position - old_position;
            if playing
                && old_playing
                && wall > 0.0
                && wall <= 3.0
                && advance > 0.0
                && advance <= wall + 0.75
            {
                self.listened += wall.min(advance);
            }
        }
        self.previous = Some((position, time, playing));
    }
    pub fn break_continuity(&mut self) {
        self.previous = None;
    }
}

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
        .unwrap()
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

pub fn settings(content: &gtk4::Box) {
    let title = gtk4::Label::new(Some("Last.fm"));
    title.set_xalign(0.0);
    content.append(&title);
    let status = gtk4::Label::new(Some("Uses your saved Ember server connection."));
    status.set_wrap(true);
    status.set_max_width_chars(45);
    content.append(&status);
    let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 6);
    let buttons: Vec<_> = [
        ("Connect", "connect"),
        ("Finish authorization", "finish"),
        ("Status", "status"),
        ("Disconnect", "disconnect"),
    ]
    .into_iter()
    .map(|(name, action)| {
        let button = gtk4::Button::with_label(name);
        row.append(&button);
        (button, action)
    })
    .collect();
    for (button, action) in &buttons {
        let status = status.clone();
        let all: Vec<_> = buttons.iter().map(|(button, _)| button.clone()).collect();
        let action = action.to_string();
        button.connect_clicked(move |_| {
            let (server, key) = match connection() {
                Ok(c) => c,
                Err(e) => {
                    status.set_text(&e);
                    return;
                }
            };
            for b in &all {
                b.set_sensitive(false);
            }
            let all = all.clone();
            let status = status.clone();
            let action = action.clone();
            glib::spawn_future_local(async move {
                let result = request(&server, &key, &json!({"action": action})).await;
                match result {
                    Ok(result) => {
                        if let Some(url) = result["url"]
                            .as_str()
                            .filter(|url| url.starts_with("https://www.last.fm/api/auth/?"))
                        {
                            match gio::AppInfo::launch_default_for_uri(
                                url,
                                None::<&gio::AppLaunchContext>,
                            ) {
                                Ok(_) => status.set_text(
                                    "Approve in the browser, then click Finish authorization.",
                                ),
                                Err(_) => status.set_text(
                                    "Could not open browser. Check your default browser setup.",
                                ),
                            }
                        } else {
                            match request(&server, &key, &json!({"action":"status"})).await {
                                Ok(v) if v["connected"] == true => status.set_text(&format!(
                                    "Connected as {} · {} pending",
                                    v["username"].as_str().unwrap_or(""),
                                    v["pending"]
                                )),
                                Ok(v) if v["configured"] == true => {
                                    status.set_text("Last.fm is disconnected.")
                                }
                                Ok(_) => status.set_text(
                                    "Set LASTFM_API_KEY and LASTFM_API_SECRET on the server first.",
                                ),
                                Err(e) => status.set_text(&e),
                            }
                        }
                    }
                    Err(e) => status.set_text(&e),
                }
                for b in all {
                    b.set_sensitive(true);
                }
            });
        });
    }
    content.append(&row);
}

pub struct Reporter {
    clock: ListeningClock,
    epoch: Instant,
    player_id: Option<u64>,
    playback_id: String,
    started: u64,
    submitted: bool,
    was_playing: bool,
    last_now: f64,
    last_attempt: f64,
    busy: bool,
    server: String,
    pending: Vec<Value>,
    connected: bool,
    account: String,
    last_status: f64,
    last_sample: f64,
}
impl Default for Reporter {
    fn default() -> Self {
        let stored = std::fs::read(crate::library::data_root().join("lastfm-outbox.json"))
            .ok()
            .filter(|b| b.len() <= 1048576)
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .unwrap_or(Value::Null);
        Self {
            clock: ListeningClock::default(),
            epoch: Instant::now(),
            player_id: None,
            playback_id: String::new(),
            started: 0,
            submitted: false,
            was_playing: false,
            last_now: f64::NEG_INFINITY,
            last_attempt: f64::NEG_INFINITY,
            busy: false,
            connected: false,
            account: stored["account"].as_str().unwrap_or("").into(),
            last_sample: f64::NEG_INFINITY,
            last_status: f64::NEG_INFINITY,
            server: stored["server"].as_str().unwrap_or("").into(),
            pending: stored["pending"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .take(1000)
                .collect(),
        }
    }
}
impl Reporter {
    pub fn break_continuity(&mut self) {
        self.clock.break_continuity();
        self.was_playing = false;
    }

    pub fn sample(
        reporter: &Rc<RefCell<Self>>,
        player_id: Option<u64>,
        track: Option<&Track>,
        position: u64,
        duration: u64,
        playing: bool,
    ) {
        let mut r = reporter.borrow_mut();
        let now = r.epoch.elapsed().as_secs_f64();
        if now - r.last_sample < 0.5 {
            return;
        }
        r.last_sample = now;
        if r.player_id != player_id {
            r.clock = ListeningClock::default();
            r.player_id = player_id;
            r.playback_id = glib::uuid_string_random().to_string();
            r.started = 0;
            r.submitted = false;
            r.last_now = f64::NEG_INFINITY;
            r.was_playing = false;
        }
        let Ok((server, key)) = connection() else {
            return;
        };
        if r.server != server {
            r.server = server.clone();
            r.pending.clear();
            r.connected = false;
            r.last_status = f64::NEG_INFINITY;
            r.clock = ListeningClock::default();
            r.save();
        }
        if !r.busy && now - r.last_status >= 60.0 {
            r.busy = true;
            r.last_status = now;
            drop(r);
            let reporter = reporter.clone();
            glib::spawn_future_local(async move {
                let result = request(&server, &key, &json!({"action":"status"})).await;
                let mut r = reporter.borrow_mut();
                if r.server == server
                    && let Ok(v) = result
                {
                    r.connected = v["connected"] == true;
                    let account = v["username"].as_str().unwrap_or("").to_owned();
                    if !r.connected || (!r.account.is_empty() && r.account != account) {
                        r.pending.clear();
                        r.clock = ListeningClock::default();
                        r.save();
                    }
                    r.account = account;
                    r.save();
                }
                r.busy = false;
            });
            return;
        }
        if !r.connected {
            r.clock.break_continuity();
            return;
        }
        r.clock.sample(position as f64 / 1000.0, now, playing);
        let mut now_body = None;
        if let Some(track) = track.filter(|_| playing) {
            if r.started == 0 {
                r.started = SystemTime::now()
                    .duration_since(UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_secs();
            }
            if !r.was_playing || now - r.last_now >= 60.0 {
                now_body = Some(
                    json!({"action":"now","title":track.name,"artist":track.artists.join(", "),"album":track.album}),
                );
                r.last_now = now;
            }
            let seconds = duration / 1000;
            if !r.submitted
                && seconds > 30
                && r.clock.listened >= (duration as f64 / 2000.0).min(240.0)
            {
                let event = json!({"action":"scrobble","id":r.playback_id,"title":track.name,"artist":track.artists.join(", "),"album":track.album,
                    "duration":seconds,"listened":r.clock.listened.ceil() as u64,"started":r.started});
                if r.pending.len() == 1000 {
                    r.pending.remove(0);
                }
                r.pending.push(event);
                r.submitted = true;
                r.save();
            }
        }
        r.was_playing = playing;
        if r.busy || (now_body.is_none() && (r.pending.is_empty() || now - r.last_attempt < 30.0)) {
            return;
        }
        let event = r.pending.first().cloned();
        r.busy = true;
        r.last_attempt = now;
        drop(r);
        let reporter = reporter.clone();
        glib::spawn_future_local(async move {
            if let Some(event) = event {
                let result = request(&server, &key, &event).await;
                let mut r = reporter.borrow_mut();
                if (result.is_ok()
                    || result
                        .as_ref()
                        .err()
                        .is_some_and(|e| e.starts_with("HTTP 400:")))
                    && r.server == server
                    && r.pending.first().map(|v| &v["id"]) == Some(&event["id"])
                {
                    r.pending.remove(0);
                    r.save();
                }
            } else if let Some(body) = now_body {
                let _ = request(&server, &key, &body).await;
            }
            reporter.borrow_mut().busy = false;
        });
    }

    fn save(&self) {
        use std::io::Write;
        use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
        let root = crate::library::data_root();
        let result = (|| -> std::io::Result<()> {
            std::fs::create_dir_all(&root)?;
            std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700))?;
            let temp = root.join(format!("lastfm-{}.tmp", glib::uuid_string_random()));
            let write = (|| -> std::io::Result<()> {
                let mut file = std::fs::OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .mode(0o600)
                    .open(&temp)?;
                file.write_all(&serde_json::to_vec(
                    &json!({"server":self.server,"account":self.account,"pending":self.pending}),
                )?)?;
                std::fs::rename(&temp, root.join("lastfm-outbox.json"))
            })();
            let _ = std::fs::remove_file(temp);
            write
        })();
        let _ = result; // Scrobble storage failure must not interrupt audio.
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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

    #[test]
    fn listening_excludes_pause_buffering_seeks_and_sleep() {
        let mut c = ListeningClock::default();
        c.sample(0.0, 0.0, true);
        c.sample(1.0, 1.0, true);
        c.sample(1.0, 2.0, false);
        c.sample(1.0, 100.0, false);
        c.sample(1.0, 101.0, true);
        c.sample(2.0, 102.0, true);
        c.sample(100.0, 103.0, true);
        c.sample(100.0, 104.0, true);
        assert_eq!(c.listened, 2.0);
        c.break_continuity();
        c.sample(101.0, 105.0, true);
        assert_eq!(c.listened, 2.0);
        c.sample(102.0, 106.0, true);
        assert_eq!(c.listened, 3.0);
    }
}
