use crate::model::Track;
use gtk4::{glib, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{cell::RefCell, rc::Rc, sync::mpsc};

#[derive(Clone, Debug, Default, Deserialize)]
pub struct LibraryState {
    pub playlists: Vec<Playlist>,
    pub detail: Option<Playlist>,
    pub pending: bool,
    pub online: bool,
    #[serde(default)]
    pub error: String,
    #[serde(default)]
    pub acknowledged: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Playlist {
    pub id: String,
    pub name: String,
    pub revision: u64,
    #[serde(rename = "trackCount")]
    pub track_count: usize,
    pub entries: Option<Vec<Entry>>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct Entry {
    pub id: String,
    pub track: LibraryTrack,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct LibraryTrack {
    pub id: String,
    pub title: String,
    pub artist: String,
    #[serde(default)]
    pub album: String,
    #[serde(default)]
    pub duration_ms: u64,
    pub artwork_url: Option<String>,
    #[serde(default = "youtube")]
    pub source: String,
}
fn youtube() -> String {
    "youtube".into()
}
fn uuid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, b)| {
            if [8, 13, 18, 23].contains(&i) {
                b == b'-'
            } else {
                b.is_ascii_digit() || (b'a'..=b'f').contains(&b)
            }
        })
}
impl LibraryTrack {
    pub fn playable(&self) -> Result<Track, String> {
        if self.source != "youtube"
            || self.artist.chars().count() > 300
            || self.artist.chars().any(char::is_control)
        {
            return Err("Invalid library track".into());
        }
        Track {
            id: self.id.clone(),
            uri: format!("https://music.youtube.com/watch?v={}", self.id),
            name: self.title.clone(),
            artists: vec![self.artist.chars().take(200).collect()],
            album: self.album.clone(),
            duration_ms: self.duration_ms,
            artwork_url: self.artwork_url.clone(),
        }
        .validate()
        .map_err(|_| "Invalid library track".into())
    }
    pub fn from_track(track: &Track) -> Self {
        Self {
            id: track.id.clone(),
            title: track.name.clone(),
            artist: track.artist_line().chars().take(300).collect(),
            album: track.album.clone(),
            duration_ms: track.duration_ms,
            artwork_url: track.artwork_url.clone(),
            source: youtube(),
        }
    }
}
impl LibraryState {
    pub fn validate(self) -> Result<Self, String> {
        if self.playlists.len() > 200 || self.error.len() > 1000 {
            return Err("Invalid library".into());
        }
        let mut ids = std::collections::HashSet::new();
        for p in &self.playlists {
            if !ids.insert(&p.id) || p.entries.is_some() {
                return Err("Invalid summary".into());
            }
            validate_playlist(p)?;
        }
        if let Some(p) = &self.detail {
            validate_playlist(p)?;
            let entries = p.entries.as_ref().ok_or("Missing entries")?;
            if entries.len() != p.track_count {
                return Err("Invalid count".into());
            }
            let mut ids = std::collections::HashSet::new();
            for e in entries {
                if !uuid(&e.id) || !ids.insert(&e.id) {
                    return Err("Invalid occurrence".into());
                }
                e.track.playable()?;
            }
        }
        Ok(self)
    }
}
fn validate_playlist(p: &Playlist) -> Result<(), String> {
    if !uuid(&p.id)
        || p.revision == 0
        || p.track_count > 5000
        || p.name.trim().is_empty()
        || p.name.chars().count() > 120
        || p.name.chars().any(char::is_control)
    {
        Err("Invalid playlist".into())
    } else {
        Ok(())
    }
}

pub enum Action {
    Request(Value),
    Play(Vec<Track>, usize),
    Queue(Track, bool),
}

pub struct LibraryPane {
    pub page: gtk4::Box,
    pub busy: bool,
    state: LibraryState,
    tx: mpsc::Sender<Action>,
    parent: gtk4::glib::WeakRef<gtk4::ApplicationWindow>,
    status: gtk4::Label,
    lists: gtk4::Box,
    songs: gtk4::Box,
    name: gtk4::Entry,
    create: gtk4::Button,
    retry: gtk4::Button,
    discard: gtk4::Button,
    edit: gtk4::Box,
    offset: usize,
    popup: Option<(gtk4::Window, gtk4::Label)>,
}

fn button(text: &str) -> gtk4::Button {
    gtk4::Button::with_label(text)
}
fn clear(container: &gtk4::Box) {
    while let Some(child) = container.first_child() {
        container.remove(&child);
    }
}

impl LibraryPane {
    pub fn new(parent: &gtk4::ApplicationWindow, tx: mpsc::Sender<Action>) -> Rc<RefCell<Self>> {
        let page = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
        page.set_margin_top(20);
        page.set_margin_bottom(16);
        page.set_margin_start(24);
        page.set_margin_end(24);
        let title = gtk4::Label::new(Some("Ember playlists"));
        title.add_css_class("page-title");
        title.set_xalign(0.0);
        page.append(&title);
        let status = gtk4::Label::new(Some(
            "Configure the server in Ember Settings, then refresh.",
        ));
        status.set_wrap(true);
        status.set_xalign(0.0);
        page.append(&status);
        let toolbar = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        let name = gtk4::Entry::new();
        name.set_placeholder_text(Some("Playlist name"));
        name.set_max_length(120);
        name.set_hexpand(true);
        let create = button("Create");
        let refresh = button("Refresh");
        let retry = button("Retry pending save");
        let discard = button("Discard pending save");
        discard.set_visible(false);
        toolbar.append(&name);
        toolbar.append(&create);
        toolbar.append(&refresh);
        toolbar.append(&retry);
        toolbar.append(&discard);
        page.append(&toolbar);
        let scroll = gtk4::ScrolledWindow::new();
        scroll.set_vexpand(true);
        scroll.set_hscrollbar_policy(gtk4::PolicyType::Never);
        let content = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
        let lists = gtk4::Box::new(gtk4::Orientation::Vertical, 4);
        content.append(&lists);
        let edit = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        let rename = button("Rename selected");
        let delete = button("Delete selected");
        edit.append(&rename);
        edit.append(&delete);
        content.append(&edit);
        let songs = gtk4::Box::new(gtk4::Orientation::Vertical, 5);
        content.append(&songs);
        scroll.set_child(Some(&content));
        page.append(&scroll);
        let pane = Rc::new(RefCell::new(Self {
            page,
            busy: false,
            state: LibraryState::default(),
            tx,
            parent: parent.downgrade(),
            status,
            lists,
            songs,
            name,
            create: create.clone(),
            retry: retry.clone(),
            discard: discard.clone(),
            edit,
            offset: 0,
            popup: None,
        }));
        let weak = Rc::downgrade(&pane);
        refresh.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::request(&p, json!({"operation":"refresh"}));
            }
        });
        let weak = Rc::downgrade(&pane);
        retry.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::request(&p, json!({"operation":"retry"}));
            }
        });
        let weak = Rc::downgrade(&pane);
        discard.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::confirm_discard(&p);
            }
        });
        let weak = Rc::downgrade(&pane);
        create.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                let name = p.borrow().name.text().to_string();
                Self::request(&p, json!({"operation":"create","name":name}));
            }
        });
        let weak = Rc::downgrade(&pane);
        rename.connect_clicked(move |_| { if let Some(p) = weak.upgrade() {
            let command = { let p = p.borrow(); p.state.detail.as_ref().map(|d| json!({"operation":"rename","id":d.id,"revision":d.revision,"name":p.name.text().to_string()})) };
            if let Some(command) = command { Self::request(&p, command); }
        }});
        let weak = Rc::downgrade(&pane);
        delete.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::confirm_delete(&p);
            }
        });
        Self::render(&pane);
        pane
    }

    pub fn request(pane: &Rc<RefCell<Self>>, command: Value) {
        let Ok(mut p) = pane.try_borrow_mut() else {
            return;
        };
        if p.busy {
            return;
        }
        p.busy = true;
        p.page.set_sensitive(false);
        if let Some((window, status)) = &p.popup {
            window.set_sensitive(false);
            status.set_text("Saving…");
        }
        p.status.set_text("Loading shared library…");
        if p.tx.send(Action::Request(command)).is_err() {
            p.busy = false;
            p.page.set_sensitive(true);
            p.status.set_text("Library worker unavailable.");
        }
    }

    pub fn apply(pane: &Rc<RefCell<Self>>, result: Result<LibraryState, String>) {
        {
            let mut p = pane.borrow_mut();
            p.busy = false;
            p.page.set_sensitive(true);
            match result {
                Ok(state) => {
                    p.state = state;
                    if let Some(detail) = &p.state.detail {
                        p.name.set_text(&detail.name);
                    }
                    if p.state.acknowledged {
                        if let Some((window, _)) = p.popup.take() {
                            window.close();
                        }
                    }
                }
                Err(_) => {
                    p.state.online = false;
                    p.state.error = "Library request interrupted. Refresh to recover any pending save before editing.".into();
                }
            }
            if let Some((window, status)) = &p.popup {
                window.set_sensitive(true);
                status.set_text(&p.state.error);
            }
        }
        Self::render(pane);
    }

    pub fn reset(pane: &Rc<RefCell<Self>>) {
        pane.borrow_mut().state = LibraryState::default();
        Self::render(pane);
        Self::request(pane, json!({"operation":"refresh"}));
    }

    fn render(pane: &Rc<RefCell<Self>>) {
        let p = pane.borrow();
        let editable = p.state.online && !p.state.pending;
        p.create.set_sensitive(editable);
        p.edit.set_sensitive(editable && p.state.detail.is_some());
        p.retry.set_visible(p.state.pending);
        p.discard.set_visible(p.state.pending);
        p.status.set_text(if !p.state.error.is_empty() {
            &p.state.error
        } else if p.state.pending {
            "A save is pending. Retry it before making another edit."
        } else if !p.state.online {
            "Cached library • read only. Refresh to reconnect."
        } else {
            "Shared with your iPhone • no provider account needed"
        });
        clear(&p.lists);
        clear(&p.songs);
        for playlist in &p.state.playlists {
            let b = button(&format!(
                "{}   ·   {} songs",
                playlist.name, playlist.track_count
            ));
            let id = playlist.id.clone();
            let weak = Rc::downgrade(pane);
            b.connect_clicked(move |_| {
                if let Some(p) = weak.upgrade() {
                    p.borrow_mut().offset = 0;
                    Self::request(&p, json!({"operation":"load","id":id}));
                }
            });
            p.lists.append(&b);
        }
        let Some(detail) = &p.state.detail else {
            return;
        };
        let entries = detail.entries.as_deref().unwrap_or_default();
        let tracks: Rc<Vec<Track>> = Rc::new(
            entries
                .iter()
                .filter_map(|e| e.track.playable().ok())
                .collect(),
        );
        let heading = gtk4::Label::new(Some(&format!("{} — {} songs", detail.name, tracks.len())));
        heading.set_xalign(0.0);
        heading.add_css_class("section-title");
        p.songs.append(&heading);
        let play = button("Play all");
        play.set_sensitive(!tracks.is_empty());
        let tx = p.tx.clone();
        let all = tracks.clone();
        play.connect_clicked(move |_| {
            let _ = tx.send(Action::Play(all.as_ref().clone(), 0));
        });
        p.songs.append(&play);
        let start = p.offset.min(entries.len().saturating_sub(1)) / 100 * 100;
        for (index, entry) in entries.iter().enumerate().skip(start).take(100) {
            let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 6);
            let play = button(&format!(
                "{}. {} — {}",
                index + 1,
                entry.track.title,
                entry.track.artist
            ));
            play.set_hexpand(true);
            play.set_halign(gtk4::Align::Fill);
            if let Some(label) = play.child().and_downcast::<gtk4::Label>() {
                label.set_ellipsize(gtk4::pango::EllipsizeMode::End);
                label.set_max_width_chars(55);
                label.set_xalign(0.0);
            }
            let tx = p.tx.clone();
            let all = tracks.clone();
            play.connect_clicked(move |_| {
                let _ = tx.send(Action::Play(all.as_ref().clone(), index));
            });
            row.append(&play);
            let next = button("Next");
            next.set_tooltip_text(Some("Play next"));
            let tx = p.tx.clone();
            let track = tracks[index].clone();
            next.connect_clicked(move |_| {
                let _ = tx.send(Action::Queue(track.clone(), true));
            });
            row.append(&next);
            for (text, operation, delta, allowed) in [
                ("↑", "move", -1, index > 0),
                ("↓", "move", 1, index + 1 < entries.len()),
                ("Remove", "remove", 0, true),
            ] {
                let b = button(text);
                b.set_sensitive(editable && allowed);
                b.set_tooltip_text(Some(match text {
                    "↑" => "Move song up",
                    "↓" => "Move song down",
                    _ => "Remove song from playlist",
                }));
                let command = json!({"operation":operation,"id":detail.id,"revision":detail.revision,"entry":entry.id,"delta":delta});
                let weak = Rc::downgrade(pane);
                b.connect_clicked(move |_| {
                    if let Some(p) = weak.upgrade() {
                        Self::request(&p, command.clone());
                    }
                });
                row.append(&b);
            }
            p.songs.append(&row);
        }
        if entries.len() > 100 {
            let pager = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
            for (text, offset, enabled) in [
                ("Previous 100", start.saturating_sub(100), start > 0),
                ("Next 100", start + 100, start + 100 < entries.len()),
            ] {
                let b = button(text);
                b.set_sensitive(enabled);
                let weak = Rc::downgrade(pane);
                b.connect_clicked(move |_| {
                    if let Some(p) = weak.upgrade() {
                        p.borrow_mut().offset = offset;
                        Self::render(&p);
                    }
                });
                pager.append(&b);
            }
            p.songs.append(&pager);
        }
    }

    fn confirm_discard(pane: &Rc<RefCell<Self>>) {
        let p = pane.borrow();
        let dialog = gtk4::AlertDialog::builder()
            .modal(true)
            .message("Discard the unconfirmed save?")
            .detail("The server may already have applied it. Refresh after discarding to see the current playlists.")
            .buttons(["Cancel", "Discard"])
            .cancel_button(0)
            .default_button(0)
            .build();
        let weak = Rc::downgrade(pane);
        dialog.choose(
            p.parent.upgrade().as_ref(),
            None::<&gtk4::gio::Cancellable>,
            move |response| {
                if response == Ok(1) {
                    if let Some(p) = weak.upgrade() {
                        Self::request(&p, json!({"operation":"discard"}));
                    }
                }
            },
        );
    }

    fn confirm_delete(pane: &Rc<RefCell<Self>>) {
        let p = pane.borrow();
        let Some(detail) = &p.state.detail else {
            return;
        };
        let dialog = gtk4::AlertDialog::builder()
            .modal(true)
            .message(format!("Delete “{}” from all devices?", detail.name))
            .detail("The songs themselves will remain available in search.")
            .buttons(["Cancel", "Delete"])
            .cancel_button(0)
            .default_button(0)
            .build();
        let command = json!({"operation":"delete","id":detail.id,"revision":detail.revision});
        let weak = Rc::downgrade(pane);
        dialog.choose(
            p.parent.upgrade().as_ref(),
            None::<&gtk4::gio::Cancellable>,
            move |response| {
                if response == Ok(1) {
                    if let Some(p) = weak.upgrade() {
                        Self::request(&p, command.clone());
                    }
                }
            },
        );
    }

    pub fn add_dialog(pane: &Rc<RefCell<Self>>, track: &Track) {
        let mut p = pane.borrow_mut();
        if p.busy {
            return;
        }
        if let Some((window, _)) = &p.popup {
            window.present();
            return;
        }
        let window = gtk4::Window::builder()
            .title("Add to Ember playlist")
            .modal(true)
            .default_width(400)
            .default_height(400)
            .build();
        if let Some(parent) = p.parent.upgrade() {
            window.set_transient_for(Some(&parent));
        }
        let content = gtk4::Box::new(gtk4::Orientation::Vertical, 10);
        content.set_margin_top(20);
        content.set_margin_bottom(20);
        content.set_margin_start(20);
        content.set_margin_end(20);
        let status = gtk4::Label::new(Some(
            "Choose a playlist. Configure or refresh Library first if none appear.",
        ));
        status.set_wrap(true);
        content.append(&status);
        let scroll = gtk4::ScrolledWindow::new();
        scroll.set_vexpand(true);
        let lists = gtk4::Box::new(gtk4::Orientation::Vertical, 5);
        for playlist in &p.state.playlists {
            let b = button(&playlist.name);
            b.set_sensitive(p.state.online && !p.state.pending && playlist.track_count < 5000);
            let command =
                json!({"operation":"add","id":playlist.id,"track":LibraryTrack::from_track(track)});
            let weak = Rc::downgrade(pane);
            b.connect_clicked(move |_| {
                if let Some(p) = weak.upgrade() {
                    Self::request(&p, command.clone());
                }
            });
            lists.append(&b);
        }
        scroll.set_child(Some(&lists));
        content.append(&scroll);
        let refresh = button("Refresh library");
        let weak = Rc::downgrade(pane);
        refresh.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                if let Some((window, _)) = p.borrow_mut().popup.take() {
                    window.close();
                }
                Self::request(&p, json!({"operation":"refresh"}));
            }
        });
        content.append(&refresh);
        window.set_child(Some(&content));
        let weak = Rc::downgrade(pane);
        window.connect_close_request(move |_| {
            if let Some(p) = weak.upgrade() {
                if let Ok(mut p) = p.try_borrow_mut() {
                    p.popup = None;
                }
            }
            glib::Propagation::Proceed
        });
        p.popup = Some((window.clone(), status));
        drop(p);
        window.present();
    }
}

pub fn data_root() -> std::path::PathBuf {
    glib::user_data_dir().join("ember-player/rust-library")
}

pub fn connection_fields() -> (String, String) {
    let value = std::fs::read(data_root().join("connection.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
        .unwrap_or(Value::Null);
    (
        value["server"].as_str().unwrap_or("").into(),
        value["key"].as_str().unwrap_or("").into(),
    )
}

pub fn save_connection(server: &str, key: &str) -> Result<(), String> {
    use std::io::Write;
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    if server.len() > 2048
        || key.is_empty()
        || key.len() > 4096
        || key.chars().any(char::is_control)
    {
        return Err("Enter a server address and valid access key.".into());
    }
    let root = data_root();
    std::fs::create_dir_all(&root).map_err(|_| "Cannot create private settings")?;
    std::fs::set_permissions(&root, std::fs::Permissions::from_mode(0o700))
        .map_err(|_| "Cannot protect settings directory")?;
    let temp = root.join(format!("connection-{}.tmp", glib::uuid_string_random()));
    let result = (|| -> std::io::Result<()> {
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&temp)?;
        file.write_all(&serde_json::to_vec(
            &json!({"server":server.trim().trim_end_matches('/'),"key":key.trim()}),
        )?)?;
        file.sync_all()?;
        std::fs::rename(&temp, root.join("connection.json"))?;
        std::fs::File::open(&root)?.sync_all()
    })();
    let _ = std::fs::remove_file(temp);
    result.map_err(|_| "Could not save private server settings.".into())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn state() -> LibraryState {
        serde_json::from_value(json!({"playlists":[{"id":"11111111-1111-4111-8111-111111111111","name":"GG","revision":1,"trackCount":2}],
          "detail":{"id":"11111111-1111-4111-8111-111111111111","name":"GG","revision":1,"trackCount":2,"entries":[
            {"id":"22222222-2222-4222-8222-222222222222","track":{"id":"dm4tkSNKfFI","title":"Awake","artist":"Tycho","durationMs":284000}},
            {"id":"33333333-3333-4333-8333-333333333333","track":{"id":"dm4tkSNKfFI","title":"Awake","artist":"Tycho","durationMs":284000}}]},"online":true,"pending":false})).unwrap()
    }
    #[test]
    fn validates_occurrences_and_maps_mobile_tracks_without_audio_urls() {
        let state = state().validate().unwrap();
        let entries = state.detail.unwrap().entries.unwrap();
        let track = entries[0].track.playable().unwrap();
        assert_eq!(track.name, "Awake");
        assert_eq!(track.artists, ["Tycho"]);
        let body = serde_json::to_value(LibraryTrack::from_track(&track)).unwrap();
        assert_eq!(body["durationMs"], 284000);
        assert!(body.get("uri").is_none());
        assert_ne!(entries[0].id, entries[1].id);
    }
    #[test]
    fn rejects_duplicate_occurrences_and_unsafe_metadata() {
        let mut duplicate = state();
        let entries = duplicate.detail.as_mut().unwrap().entries.as_mut().unwrap();
        entries[1].id = entries[0].id.clone();
        assert!(duplicate.validate().is_err());
        let mut unsafe_state = state();
        unsafe_state
            .detail
            .as_mut()
            .unwrap()
            .entries
            .as_mut()
            .unwrap()[0]
            .track
            .artwork_url = Some("https://evil.test/art".into());
        assert!(unsafe_state.validate().is_err());
    }
}
