use crate::artwork::{ArtworkLoader, ArtworkSlot, artwork_view};
use crate::model::Track;
use gtk4::{gdk, glib, prelude::*};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use std::{
    cell::RefCell,
    collections::{HashMap, HashSet},
    rc::Rc,
    sync::mpsc,
    time::{Duration, Instant},
};

#[derive(Default)]
struct TypeJump {
    query: String,
    last_key: Option<Instant>,
    matched: Option<usize>,
}

impl TypeJump {
    fn find(&mut self, key: char, now: Instant, tracks: &[Track]) -> Option<usize> {
        if self
            .last_key
            .is_none_or(|last| now.duration_since(last) > Duration::from_secs(1))
        {
            self.query.clear();
            self.matched = None;
        }
        let key = key.to_lowercase().collect::<String>();
        let cycle = self.query == key;
        if !cycle && self.query.chars().count() < 120 {
            self.query.push_str(&key);
        }
        self.last_key = Some(now);
        let start = if cycle {
            self.matched.map_or(0, |i| i + 1)
        } else {
            0
        };
        // Prefer song-title prefixes, then words anywhere in the title or artist.
        let found = [true, false].into_iter().find_map(|prefix| {
            (0..tracks.len())
                .map(|offset| (start + offset) % tracks.len())
                .find(|&i| {
                    let title = tracks[i].name.to_lowercase();
                    if prefix {
                        title.starts_with(&self.query)
                    } else {
                        title.contains(&self.query)
                            || tracks[i].artist_line().to_lowercase().contains(&self.query)
                    }
                })
        });
        if found.is_some() {
            self.matched = found;
        }
        found
    }
}

const PLAYLIST_COVER_SIZE: i32 = 192;
const MAX_COVER_ART: usize = 4;

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
fn valid_name(value: &str) -> bool {
    let value = value.trim();
    !value.is_empty() && value.chars().count() <= 120 && !value.chars().any(char::is_control)
}
fn count_label(count: usize) -> String {
    format!("{count} {}", if count == 1 { "song" } else { "songs" })
}
fn cover_urls(entries: &[Entry]) -> Vec<String> {
    let mut seen = HashSet::new();
    let mut urls = Vec::new();
    for entry in entries {
        if let Some(url) = entry
            .track
            .artwork_url
            .as_deref()
            .filter(|url| !url.is_empty())
            && seen.insert(url.to_owned())
        {
            urls.push(url.to_owned());
            if urls.len() == MAX_COVER_ART {
                break;
            }
        }
    }
    urls
}
fn playlist_initial(name: &str) -> String {
    name.trim()
        .chars()
        .find(|character| character.is_alphanumeric())
        .map(|character| character.to_uppercase().to_string())
        .unwrap_or_else(|| "♪".into())
}
fn shuffled_tracks(tracks: &[Track]) -> Vec<Track> {
    let mut shuffled = tracks.to_vec();
    for index in (1..shuffled.len()).rev() {
        let other = glib::random_int_range(0, (index + 1) as i32) as usize;
        shuffled.swap(index, other);
    }
    shuffled
}

fn playlist_meta(entries: &[Entry]) -> String {
    if entries.is_empty() {
        return "Empty playlist".into();
    }
    let total: u64 = entries.iter().map(|entry| entry.track.duration_ms).sum();
    let minutes = total.div_ceil(60_000).max(1);
    format!("{} · {minutes} min", count_label(entries.len()))
}

pub enum Action {
    Request(Value),
    OpenPlaylist(String),
    Play(Vec<Track>, usize),
    Queue(Track, bool),
    Radio(Track),
}

pub struct LibraryPane {
    pub page: gtk4::Box,
    pub busy: bool,
    state: LibraryState,
    tx: mpsc::Sender<Action>,
    parent: glib::WeakRef<gtk4::ApplicationWindow>,
    artwork_loader: Rc<RefCell<ArtworkLoader>>,
    status: gtk4::Label,
    sidebar_list: gtk4::Box,
    home_section: gtk4::Box,
    home_flow: gtk4::FlowBox,
    home_count: gtk4::Label,
    songs: gtk4::Box,
    add: gtk4::Button,
    retry: gtk4::Button,
    discard: gtk4::Button,
    scroll: gtk4::ScrolledWindow,
    rows: gtk4::Box,
    rendered: usize,
    type_jump: TypeJump,
    playlist_tracks: Rc<Vec<Track>>,
    popup: Option<(gtk4::Window, gtk4::Label)>,
    cover_art: HashMap<String, Vec<String>>,
}

fn button(text: &str) -> gtk4::Button {
    gtk4::Button::with_label(text)
}
fn action_button(icon: &str, accessible: &str) -> gtk4::Button {
    let button = gtk4::Button::from_icon_name(icon);
    button.add_css_class("library-action");
    button.set_tooltip_text(Some(accessible));
    button.update_property(&[gtk4::accessible::Property::Label(accessible)]);
    button
}
fn label(text: &str, class: &str) -> gtk4::Label {
    let label = gtk4::Label::new(Some(text));
    label.set_xalign(0.0);
    if !class.is_empty() {
        label.add_css_class(class);
    }
    label
}
fn ellipsized(label: &gtk4::Label, width: i32) {
    label.set_ellipsize(gtk4::pango::EllipsizeMode::End);
    label.set_max_width_chars(width);
}
fn clear(container: &gtk4::Box) {
    while let Some(child) = container.first_child() {
        container.remove(&child);
    }
}
fn clear_flow(flow: &gtk4::FlowBox) {
    while let Some(child) = flow.first_child() {
        flow.remove(&child);
    }
}
fn close_on_escape(window: &gtk4::Window) {
    let controller = gtk4::EventControllerKey::new();
    let target = window.downgrade();
    controller.connect_key_pressed(move |_, key, _, _| {
        if key == gdk::Key::Escape {
            if let Some(window) = target.upgrade() {
                if window.is_sensitive() {
                    window.close();
                }
                return glib::Propagation::Stop;
            }
        }
        glib::Propagation::Proceed
    });
    window.add_controller(controller);
}
fn close_button<F>(text: &str, window: &gtk4::Window, on_close: F) -> gtk4::Button
where
    F: Fn() + 'static,
{
    let close = button(text);
    let target = window.downgrade();
    close.connect_clicked(move |_| {
        let Some(window) = target.upgrade() else {
            return;
        };
        on_close();
        window.close();
    });
    close
}
fn dialog_window(
    parent: &glib::WeakRef<gtk4::ApplicationWindow>,
    title: &str,
    width: i32,
) -> gtk4::Window {
    let window = gtk4::Window::builder()
        .title(title)
        .modal(true)
        .deletable(false)
        .default_width(width)
        .resizable(false)
        .build();
    if let Some(parent) = parent.upgrade() {
        window.set_transient_for(Some(&parent));
    }
    window
}

fn cover_tile(url: &str, loader: &Rc<RefCell<ArtworkLoader>>) -> gtk4::Overlay {
    let overlay = gtk4::Overlay::new();
    overlay.add_css_class("cover-tile");
    overlay.set_hexpand(true);
    overlay.set_vexpand(true);
    let placeholder = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    placeholder.add_css_class("cover-placeholder");
    placeholder.set_hexpand(true);
    placeholder.set_vexpand(true);
    let glyph = gtk4::Image::from_icon_name("media-optical-symbolic");
    glyph.set_pixel_size(18);
    glyph.add_css_class("cover-glyph");
    glyph.set_halign(gtk4::Align::Center);
    glyph.set_valign(gtk4::Align::Center);
    glyph.set_vexpand(true);
    placeholder.append(&glyph);
    overlay.set_child(Some(&placeholder));
    let picture = gtk4::Picture::new();
    picture.add_css_class("cover-picture");
    picture.set_can_shrink(true);
    picture.set_content_fit(gtk4::ContentFit::Cover);
    picture.set_hexpand(true);
    picture.set_vexpand(true);
    overlay.add_overlay(&picture);
    let slot = ArtworkSlot::new(&picture);
    ArtworkLoader::request(loader, url, slot);
    overlay
}
fn cover_fallback(name: &str, large: bool) -> gtk4::Box {
    let tile = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    tile.add_css_class("playlist-cover-fallback");
    tile.set_hexpand(true);
    tile.set_vexpand(true);
    let initial = label(
        &playlist_initial(name),
        if large {
            "cover-initial"
        } else {
            "cover-initial small"
        },
    );
    initial.set_halign(gtk4::Align::Center);
    initial.set_valign(gtk4::Align::Center);
    initial.set_hexpand(true);
    initial.set_vexpand(true);
    tile.append(&initial);
    tile
}
fn cover_body(
    urls: &[String],
    name: &str,
    large: bool,
    loader: &Rc<RefCell<ArtworkLoader>>,
) -> gtk4::Grid {
    let grid = gtk4::Grid::new();
    grid.add_css_class("playlist-cover");
    grid.set_hexpand(true);
    grid.set_vexpand(true);
    grid.set_column_homogeneous(true);
    grid.set_row_homogeneous(true);
    grid.set_column_spacing(1);
    grid.set_row_spacing(1);
    let urls: Vec<&String> = urls.iter().take(MAX_COVER_ART).collect();
    if urls.is_empty() {
        grid.attach(&cover_fallback(name, large), 0, 0, 2, 2);
        return grid;
    }
    let placements: Vec<(i32, i32, i32, i32)> = match urls.len() {
        1 => vec![(0, 0, 2, 2)],
        2 => vec![(0, 0, 2, 1), (0, 1, 2, 1)],
        3 => vec![(0, 0, 2, 1), (0, 1, 1, 1), (1, 1, 1, 1)],
        _ => vec![(0, 0, 1, 1), (1, 0, 1, 1), (0, 1, 1, 1), (1, 1, 1, 1)],
    };
    for (index, url) in urls.iter().enumerate() {
        let (left, top, width, height) = placements[index];
        grid.attach(&cover_tile(url, loader), left, top, width, height);
    }
    grid
}
fn fixed_cover(
    urls: &[String],
    name: &str,
    size: i32,
    large: bool,
    loader: &Rc<RefCell<ArtworkLoader>>,
) -> gtk4::Box {
    let wrapper = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    wrapper.add_css_class("playlist-cover-wrap");
    wrapper.set_size_request(size, size);
    wrapper.set_hexpand(false);
    wrapper.set_vexpand(false);
    wrapper.set_halign(gtk4::Align::Start);
    wrapper.set_valign(gtk4::Align::Start);
    wrapper.append(&cover_body(urls, name, large, loader));
    wrapper
}
fn card_cover(
    urls: &[String],
    name: &str,
    loader: &Rc<RefCell<ArtworkLoader>>,
) -> crate::artwork::SquareCover {
    let square: crate::artwork::SquareCover = glib::Object::new();
    square.add_css_class("playlist-card-cover");
    square.set_hexpand(true);
    square.set_halign(gtk4::Align::Fill);
    square.set_valign(gtk4::Align::Start);
    cover_body(urls, name, true, loader).set_parent(&square);
    square
}
fn playlist_count_label(count: usize) -> String {
    count_label(count)
}
fn playlist_card(
    playlist: &Playlist,
    urls: &[String],
    loader: &Rc<RefCell<ArtworkLoader>>,
    tx: &mpsc::Sender<Action>,
) -> gtk4::Button {
    let card = gtk4::Button::new();
    card.add_css_class("playlist-card");
    card.set_hexpand(true);
    card.set_halign(gtk4::Align::Fill);
    card.set_valign(gtk4::Align::Start);
    card.set_tooltip_text(Some(&playlist.name));
    let content = gtk4::Box::new(gtk4::Orientation::Vertical, 7);
    content.set_hexpand(true);
    content.set_halign(gtk4::Align::Fill);
    content.append(&card_cover(urls, &playlist.name, loader));
    let title = label(&playlist.name, "card-title");
    ellipsized(&title, 26);
    title.set_tooltip_text(Some(&playlist.name));
    let meta = label(&playlist_count_label(playlist.track_count), "card-artist");
    content.append(&title);
    content.append(&meta);
    card.set_child(Some(&content));
    card.update_property(&[gtk4::accessible::Property::Label(&format!(
        "Open playlist {}, {}",
        playlist.name,
        count_label(playlist.track_count)
    ))]);
    let id = playlist.id.clone();
    let tx = tx.clone();
    card.connect_clicked(move |_| {
        let _ = tx.send(Action::OpenPlaylist(id.clone()));
    });
    card
}
fn sidebar_playlist_row(
    playlist: &Playlist,
    selected: bool,
    tx: &mpsc::Sender<Action>,
) -> gtk4::Button {
    let row_button = gtk4::Button::new();
    row_button.add_css_class("playlist-nav-row");
    row_button.set_hexpand(true);
    row_button.set_halign(gtk4::Align::Fill);
    if selected {
        row_button.add_css_class("selected");
    }
    let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    let text = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    text.set_hexpand(true);
    let name = label(&playlist.name, "playlist-nav-name");
    name.set_hexpand(true);
    ellipsized(&name, 18);
    let meta = label(
        &playlist_count_label(playlist.track_count),
        "playlist-nav-meta",
    );
    text.append(&name);
    text.append(&meta);
    row.append(&text);
    row_button.set_child(Some(&row));
    row_button.update_property(&[gtk4::accessible::Property::Label(&format!(
        "Open playlist {}, {}",
        playlist.name,
        count_label(playlist.track_count)
    ))]);
    let id = playlist.id.clone();
    let tx = tx.clone();
    row_button.connect_clicked(move |_| {
        let _ = tx.send(Action::OpenPlaylist(id.clone()));
    });
    row_button
}

fn menu_is_open(widget: &gtk4::Widget) -> bool {
    if widget
        .downcast_ref::<gtk4::MenuButton>()
        .is_some_and(|button| button.is_active())
    {
        return true;
    }
    let mut child = widget.first_child();
    while let Some(current) = child {
        if menu_is_open(&current) {
            return true;
        }
        child = current.next_sibling();
    }
    false
}

fn close_popover(popover: &glib::WeakRef<gtk4::Popover>) {
    if let Some(popover) = popover.upgrade() {
        popover.popdown();
    }
}

fn playlist_song_row(
    pane: &Rc<RefCell<LibraryPane>>,
    detail_id: &str,
    revision: u64,
    entry: &Entry,
    index: usize,
    total: usize,
    tracks: &Rc<Vec<Track>>,
    editable: bool,
    loader: &Rc<RefCell<ArtworkLoader>>,
    tx: &mpsc::Sender<Action>,
) -> gtk4::Box {
    let Some(track) = tracks.get(index).cloned() else {
        return gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    };
    let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 4);
    row.add_css_class("library-song");
    row.set_valign(gtk4::Align::Start);
    row.set_vexpand(false);

    let play = gtk4::Button::new();
    play.add_css_class("song-row-main");
    play.set_hexpand(true);
    play.set_halign(gtk4::Align::Fill);
    play.set_valign(gtk4::Align::Center);
    play.set_vexpand(false);
    play.set_tooltip_text(Some(&format!("Play {}", entry.track.title)));
    let content = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
    content.set_valign(gtk4::Align::Center);
    let (artwork_frame, artwork_picture) = artwork_view(40, 14);
    artwork_frame.set_valign(gtk4::Align::Center);
    let slot = ArtworkSlot::new(&artwork_picture);
    if let Some(url) = entry.track.artwork_url.as_deref() {
        ArtworkLoader::request(loader, url, slot);
    }
    content.append(&artwork_frame);
    let text = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
    text.set_hexpand(true);
    text.set_valign(gtk4::Align::Center);
    let title = label(&entry.track.title, "song-title");
    title.set_hexpand(true);
    ellipsized(&title, 54);
    let artist = label(&entry.track.artist, "song-artist");
    artist.set_hexpand(true);
    ellipsized(&artist, 54);
    text.append(&title);
    text.append(&artist);
    content.append(&text);
    let duration = label(&track.duration_text(), "song-duration");
    duration.set_valign(gtk4::Align::Center);
    content.append(&duration);
    play.set_child(Some(&content));
    play.update_property(&[gtk4::accessible::Property::Label(&format!(
        "Play {} by {}",
        entry.track.title, entry.track.artist
    ))]);
    let all = tracks.clone();
    let play_tx = tx.clone();
    play.connect_clicked(move |_| {
        let _ = play_tx.send(Action::Play(all.as_ref().clone(), index));
    });
    row.append(&play);

    let menu = gtk4::MenuButton::new();
    menu.add_css_class("row-menu");
    menu.set_child(Some(&gtk4::Image::from_icon_name("view-more-symbolic")));
    menu.set_tooltip_text(Some("Song options"));
    menu.set_valign(gtk4::Align::Center);
    menu.update_property(&[gtk4::accessible::Property::Label("Song options")]);
    let popover = gtk4::Popover::new();
    popover.add_css_class("track-menu");
    popover.set_has_arrow(false);
    let choices = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
    let next = button("Play next");
    next.add_css_class("menu-action");
    let add = button("Add to queue");
    add.add_css_class("menu-action");
    let radio = button("Song radio");
    radio.add_css_class("menu-action");
    let up = button("Move up");
    up.add_css_class("menu-action");
    up.set_sensitive(editable && index > 0);
    let down = button("Move down");
    down.add_css_class("menu-action");
    down.set_sensitive(editable && index + 1 < total);
    let remove = button("Remove from playlist");
    remove.add_css_class("menu-action");
    remove.add_css_class("destructive");
    remove.set_sensitive(editable);
    for item in [&next, &add, &radio, &up, &down, &remove] {
        choices.append(item);
    }
    popover.set_child(Some(&choices));
    let popover_weak = popover.downgrade();
    let radio_tx = tx.clone();
    let radio_track = track.clone();
    let radio_popover = popover.downgrade();
    radio.connect_clicked(move |_| {
        let _ = radio_tx.send(Action::Radio(radio_track.clone()));
        close_popover(&radio_popover);
    });
    let next_tx = tx.clone();
    let next_track = track.clone();
    next.connect_clicked(move |_| {
        let _ = next_tx.send(Action::Queue(next_track.clone(), true));
        close_popover(&popover_weak);
    });
    let popover_weak = popover.downgrade();
    let add_tx = tx.clone();
    let add_track = track.clone();
    add.connect_clicked(move |_| {
        let _ = add_tx.send(Action::Queue(add_track.clone(), false));
        close_popover(&popover_weak);
    });
    for (button, operation, delta) in [
        (&up, "move", -1),
        (&down, "move", 1),
        (&remove, "remove", 0),
    ] {
        let command = json!({
            "operation": operation,
            "id": detail_id,
            "revision": revision,
            "entry": entry.id,
            "delta": delta
        });
        let weak = Rc::downgrade(pane);
        let popover_weak = popover.downgrade();
        button.connect_clicked(move |_| {
            if let Some(pane) = weak.upgrade() {
                LibraryPane::request(&pane, command.clone());
            }
            close_popover(&popover_weak);
        });
    }
    menu.set_popover(Some(&popover));
    row.append(&menu);
    row
}

impl LibraryPane {
    pub fn new(
        parent: &gtk4::ApplicationWindow,
        tx: mpsc::Sender<Action>,
        artwork_loader: Rc<RefCell<ArtworkLoader>>,
        sidebar_slot: &gtk4::Box,
        home_slot: &gtk4::Box,
    ) -> Rc<RefCell<Self>> {
        let page = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
        page.add_css_class("library-page");
        let toolbar = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        toolbar.add_css_class("library-toolbar");
        toolbar.set_margin_start(26);
        toolbar.set_margin_end(26);
        toolbar.set_margin_top(18);
        toolbar.set_margin_bottom(2);
        let title = label("Library", "page-title");
        title.set_hexpand(true);
        let refresh = action_button("view-refresh-symbolic", "Refresh library");
        refresh.set_valign(gtk4::Align::Center);
        toolbar.append(&title);
        toolbar.append(&refresh);
        page.append(&toolbar);

        let status = label(
            "Configure the server in Ember Settings, then refresh.",
            "library-status",
        );
        status.set_wrap(true);
        status.set_margin_start(26);
        status.set_margin_end(26);
        status.set_margin_bottom(5);
        page.append(&status);

        let recovery = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        recovery.set_halign(gtk4::Align::Start);
        recovery.set_margin_start(26);
        recovery.set_margin_end(26);
        recovery.set_margin_bottom(6);
        let retry = button("Retry pending save");
        retry.add_css_class("library-secondary");
        retry.set_visible(false);
        let discard = button("Discard pending save");
        discard.add_css_class("library-secondary");
        discard.set_visible(false);
        recovery.append(&retry);
        recovery.append(&discard);
        page.append(&recovery);

        let scroll = gtk4::ScrolledWindow::new();
        scroll.set_vexpand(true);
        scroll.set_hscrollbar_policy(gtk4::PolicyType::Never);
        scroll.add_css_class("page-scroll");
        let songs = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
        songs.add_css_class("library-content");
        songs.set_margin_start(26);
        songs.set_margin_end(26);
        songs.set_margin_bottom(26);
        scroll.set_child(Some(&songs));
        page.append(&scroll);

        let sidebar = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
        sidebar.add_css_class("sidebar-playlists");
        sidebar.set_vexpand(true);
        let sidebar_header = gtk4::Box::new(gtk4::Orientation::Horizontal, 6);
        sidebar_header.add_css_class("sidebar-playlists-header");
        sidebar_header.set_margin_start(18);
        sidebar_header.set_margin_end(9);
        sidebar_header.set_margin_top(12);
        sidebar_header.set_margin_bottom(5);
        let playlist_title = label("Playlists", "sidebar-section");
        playlist_title.set_hexpand(true);
        let add = action_button("list-add-symbolic", "New playlist");
        add.add_css_class("sidebar-add");
        add.set_valign(gtk4::Align::Center);
        sidebar_header.append(&playlist_title);
        sidebar_header.append(&add);
        sidebar.append(&sidebar_header);
        let sidebar_scroll = gtk4::ScrolledWindow::new();
        sidebar_scroll.set_vexpand(true);
        sidebar_scroll.set_hscrollbar_policy(gtk4::PolicyType::Never);
        sidebar_scroll.add_css_class("sidebar-playlists-scroll");
        let sidebar_list = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
        sidebar_list.add_css_class("sidebar-playlist-list");
        sidebar_list.set_margin_start(9);
        sidebar_list.set_margin_end(9);
        sidebar_list.set_margin_bottom(9);
        sidebar_scroll.set_child(Some(&sidebar_list));
        sidebar.append(&sidebar_scroll);
        sidebar_slot.append(&sidebar);

        let home_section = gtk4::Box::new(gtk4::Orientation::Vertical, 13);
        home_section.add_css_class("home-playlists-section");
        home_section.set_visible(false);
        let home_header = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
        home_header.append(&label("Shared playlists", "section-title"));
        let home_count = label("", "page-meta");
        home_count.set_hexpand(true);
        home_count.set_halign(gtk4::Align::End);
        home_header.append(&home_count);
        home_section.append(&home_header);
        let home_flow = gtk4::FlowBox::new();
        home_flow.add_css_class("playlist-flow");
        home_flow.set_selection_mode(gtk4::SelectionMode::None);
        home_flow.set_homogeneous(true);
        home_flow.set_min_children_per_line(2);
        home_flow.set_max_children_per_line(4);
        home_flow.set_column_spacing(18);
        home_flow.set_row_spacing(22);
        home_flow.set_valign(gtk4::Align::Start);
        home_section.append(&home_flow);
        home_slot.append(&home_section);

        let pane = Rc::new(RefCell::new(Self {
            page,
            busy: false,
            state: LibraryState::default(),
            tx,
            parent: parent.downgrade(),
            artwork_loader,
            status,
            sidebar_list,
            home_section,
            home_flow,
            home_count,
            songs,
            add: add.clone(),
            retry: retry.clone(),
            discard: discard.clone(),
            scroll: scroll.clone(),
            rows: gtk4::Box::new(gtk4::Orientation::Vertical, 0),
            rendered: 0,
            type_jump: TypeJump::default(),
            playlist_tracks: Rc::new(Vec::new()),
            popup: None,
            cover_art: HashMap::new(),
        }));
        let keys = gtk4::EventControllerKey::new();
        keys.set_propagation_phase(gtk4::PropagationPhase::Capture);
        let weak = Rc::downgrade(&pane);
        keys.connect_key_pressed(move |_, key, _, modifiers| {
            let Some(pane) = weak.upgrade() else {
                return glib::Propagation::Proceed;
            };
            let mut p = pane.borrow_mut();
            if !p.page.is_mapped()
                || modifiers.intersects(
                    gdk::ModifierType::CONTROL_MASK
                        | gdk::ModifierType::ALT_MASK
                        | gdk::ModifierType::SUPER_MASK,
                )
                || menu_is_open(p.page.upcast_ref())
            {
                return glib::Propagation::Proceed;
            }
            if let Some(parent) = p.parent.upgrade() {
                if gtk4::prelude::GtkWindowExt::focus(&parent)
                    .is_some_and(|focus| focus.is::<gtk4::Editable>())
                {
                    return glib::Propagation::Proceed;
                }
            }
            if key == gdk::Key::Escape {
                p.type_jump = TypeJump::default();
                return glib::Propagation::Proceed;
            }
            let Some(ch) = key.to_unicode().filter(|ch| !ch.is_control()) else {
                return glib::Propagation::Proceed;
            };
            if p.playlist_tracks.is_empty()
                || (ch.is_whitespace()
                    && (p.type_jump.query.is_empty()
                        || p.type_jump
                            .last_key
                            .is_none_or(|last| last.elapsed() > Duration::from_secs(1))))
            {
                return glib::Propagation::Proceed;
            }
            p.jump_to_song(&pane, ch);
            glib::Propagation::Stop
        });
        parent.add_controller(keys);
        let weak = Rc::downgrade(&pane);
        refresh.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::request(&p, json!({"operation":"refresh"}));
            }
        });
        let weak = Rc::downgrade(&pane);
        add.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::open_create(&p);
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
        scroll
            .vadjustment()
            .connect_value_changed(move |adjustment| {
                let Some(pane) = weak.upgrade() else {
                    return;
                };
                let Ok(mut p) = pane.try_borrow_mut() else {
                    return;
                };
                if p.busy
                    || adjustment.page_size() <= 0.0
                    || adjustment.value() + adjustment.page_size() + 400.0 < adjustment.upper()
                {
                    return;
                }
                let editable = p.state.online && !p.state.pending;
                if let Some(detail) = p.state.detail.take() {
                    p.append_rows(&pane, &detail, editable);
                    p.state.detail = Some(detail);
                }
            });
        Self::render(&pane);
        pane
    }

    pub fn load(pane: &Rc<RefCell<Self>>, id: &str) {
        if let Ok(p) = pane.try_borrow() {
            p.scroll.vadjustment().set_value(0.0);
        }
        Self::request(pane, json!({"operation":"load","id":id}));
    }

    pub fn refresh_on_focus(pane: &Rc<RefCell<Self>>) {
        let weak = Rc::downgrade(pane);
        // Let the activating click open its menu before deciding to refresh.
        glib::idle_add_local_once(move || {
            let Some(pane) = weak.upgrade() else {
                return;
            };
            let Ok(p) = pane.try_borrow() else {
                return;
            };
            if p.busy
                || p.popup.is_some()
                || !p.page.is_mapped()
                || !p.parent.upgrade().is_some_and(|window| window.is_active())
                || menu_is_open(p.page.upcast_ref())
            {
                return;
            }
            drop(p);
            Self::request(&pane, json!({"operation":"refresh"}));
        });
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
            if let Some((window, _)) = &p.popup {
                window.set_sensitive(true);
            }
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
                    let acknowledged = state.acknowledged;
                    p.state = state;
                    if acknowledged {
                        if let Some((window, _)) = p.popup.take() {
                            window.close();
                        }
                    }
                }
                Err(_) => {
                    p.state.online = false;
                    p.state.error =
                        "Library request interrupted. Refresh to recover any pending save before editing."
                            .into();
                }
            }
            if let Some((window, status)) = &p.popup {
                window.set_sensitive(true);
                let message = if !p.state.error.is_empty() {
                    p.state.error.clone()
                } else if p.state.pending {
                    "Save is pending. Close this dialog, then retry or discard it in Library."
                        .into()
                } else if !p.state.online {
                    "Connection lost. Close and refresh Library.".into()
                } else {
                    "Saved.".into()
                };
                status.set_text(&message);
            }
        }
        Self::render(pane);
    }

    pub fn reset(pane: &Rc<RefCell<Self>>) {
        {
            let mut p = pane.borrow_mut();
            p.state = LibraryState::default();
            p.cover_art.clear();
        }
        Self::render(pane);
        Self::request(pane, json!({"operation":"refresh"}));
    }

    fn render(pane: &Rc<RefCell<Self>>) {
        let mut p = pane.borrow_mut();
        let editable = p.state.online && !p.state.pending;
        p.add.set_sensitive(editable);
        p.retry.set_visible(p.state.pending);
        p.discard.set_visible(p.state.pending);
        p.status.set_text(if !p.state.error.is_empty() {
            &p.state.error
        } else if p.state.pending {
            "A save is pending. Retry it before making another edit."
        } else if !p.state.online {
            "Cached library · read only. Refresh to reconnect."
        } else {
            "Shared with your iPhone · no provider account needed"
        });
        let detail = p.state.detail.take();
        let selected = detail.as_ref().map(|detail| detail.id.clone());
        let selected_cover = detail.as_ref().map(|detail| {
            (
                detail.id.clone(),
                cover_urls(detail.entries.as_deref().unwrap_or_default()),
            )
        });
        Self::render_sidebar(&mut p, selected.as_deref(), selected_cover);
        Self::render_home(&mut p);
        Self::render_page(&mut p, pane, detail.as_ref(), editable);
        p.state.detail = detail;
    }

    fn render_sidebar(
        &mut self,
        selected: Option<&str>,
        selected_cover: Option<(String, Vec<String>)>,
    ) {
        clear(&self.sidebar_list);
        self.cover_art.retain(|id, _| {
            self.state
                .playlists
                .iter()
                .any(|p| &p.id == id && p.track_count > 0)
        });
        if let Some((id, urls)) = selected_cover {
            if urls.is_empty() {
                self.cover_art.remove(&id);
            } else {
                self.cover_art.insert(id, urls);
            }
        }
        let playlists = self.state.playlists.clone();
        if playlists.is_empty() {
            self.sidebar_list
                .append(&label("No playlists yet", "sidebar-empty"));
            return;
        }
        for playlist in &playlists {
            let row =
                sidebar_playlist_row(playlist, selected == Some(playlist.id.as_str()), &self.tx);
            self.sidebar_list.append(&row);
        }
    }

    fn render_home(&mut self) {
        clear_flow(&self.home_flow);
        let playlists = self.state.playlists.clone();
        if playlists.is_empty() {
            self.home_section.set_visible(false);
            self.home_count.set_text("");
            return;
        }
        self.home_section.set_visible(true);
        self.home_count.set_text(&format!(
            "{0} {1}",
            playlists.len(),
            if playlists.len() == 1 {
                "playlist"
            } else {
                "playlists"
            }
        ));
        for playlist in &playlists {
            let urls = self
                .cover_art
                .get(&playlist.id)
                .cloned()
                .unwrap_or_default();
            let card = playlist_card(playlist, &urls, &self.artwork_loader, &self.tx);
            self.home_flow.insert(&card, -1);
        }
    }

    fn render_page(&mut self, pane: &Rc<RefCell<Self>>, detail: Option<&Playlist>, editable: bool) {
        clear(&self.songs);
        clear(&self.rows);
        self.rendered = 0;
        self.type_jump = TypeJump::default();
        self.playlist_tracks = Rc::new(Vec::new());
        let Some(detail) = detail else {
            self.render_overview();
            return;
        };
        let entries = detail.entries.as_deref().unwrap_or_default();
        let urls = cover_urls(entries);
        let hero = gtk4::Box::new(gtk4::Orientation::Horizontal, 22);
        hero.add_css_class("playlist-hero");
        hero.set_hexpand(true);
        hero.set_valign(gtk4::Align::Start);
        let cover = fixed_cover(
            &urls,
            &detail.name,
            PLAYLIST_COVER_SIZE,
            true,
            &self.artwork_loader,
        );
        hero.append(&cover);
        let info = gtk4::Box::new(gtk4::Orientation::Vertical, 8);
        info.add_css_class("playlist-hero-info");
        info.set_hexpand(true);
        info.set_valign(gtk4::Align::Center);
        let eyebrow = label("SHARED PLAYLIST", "playlist-eyebrow");
        let heading = label(&detail.name, "playlist-hero-title");
        heading.set_hexpand(true);
        heading.set_ellipsize(gtk4::pango::EllipsizeMode::End);
        heading.set_max_width_chars(44);
        heading.set_tooltip_text(Some(&detail.name));
        let meta = label(&playlist_meta(entries), "playlist-hero-meta");
        info.append(&eyebrow);
        info.append(&heading);
        info.append(&meta);
        let tracks: Rc<Vec<Track>> = Rc::new(
            entries
                .iter()
                .filter_map(|entry| entry.track.playable().ok())
                .collect(),
        );
        let actions = gtk4::Box::new(gtk4::Orientation::Horizontal, 7);
        actions.add_css_class("playlist-hero-actions");
        let play = gtk4::Button::new();
        play.add_css_class("accent");
        play.add_css_class("playlist-play");
        let play_content = gtk4::Box::new(gtk4::Orientation::Horizontal, 7);
        let play_icon = gtk4::Image::from_icon_name("media-playback-start-symbolic");
        play_icon.set_pixel_size(13);
        play_content.append(&play_icon);
        play_content.append(&label("Play", "playlist-play-label"));
        play.set_child(Some(&play_content));
        play.set_sensitive(!tracks.is_empty());
        play.set_tooltip_text(Some("Play this playlist"));
        play.update_property(&[gtk4::accessible::Property::Label("Play playlist")]);
        let play_tx = self.tx.clone();
        let all_tracks = tracks.clone();
        play.connect_clicked(move |_| {
            let _ = play_tx.send(Action::Play(all_tracks.as_ref().clone(), 0));
        });
        actions.append(&play);
        let shuffle = gtk4::Button::new();
        shuffle.add_css_class("playlist-play");
        shuffle.add_css_class("playlist-shuffle");
        let shuffle_content = gtk4::Box::new(gtk4::Orientation::Horizontal, 7);
        let shuffle_icon = gtk4::Image::from_icon_name("media-playlist-shuffle-symbolic");
        shuffle_icon.set_pixel_size(13);
        shuffle_content.append(&shuffle_icon);
        shuffle_content.append(&label("Shuffle", "playlist-play-label"));
        shuffle.set_child(Some(&shuffle_content));
        shuffle.set_sensitive(!tracks.is_empty());
        shuffle.set_tooltip_text(Some("Play this playlist in random order"));
        shuffle.update_property(&[gtk4::accessible::Property::Label("Shuffle playlist")]);
        let shuffle_tx = self.tx.clone();
        let shuffle_tracks = tracks.clone();
        shuffle.connect_clicked(move |_| {
            let _ = shuffle_tx.send(Action::Play(shuffled_tracks(&shuffle_tracks), 0));
        });
        actions.append(&shuffle);

        let options = gtk4::MenuButton::new();
        options.add_css_class("library-action");
        options.add_css_class("playlist-options");
        options.set_child(Some(&gtk4::Image::from_icon_name("view-more-symbolic")));
        options.set_tooltip_text(Some("Playlist options"));
        options.update_property(&[gtk4::accessible::Property::Label("Playlist options")]);
        let options_popover = gtk4::Popover::new();
        options_popover.add_css_class("track-menu");
        options_popover.set_has_arrow(false);
        let choices = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
        let rename = button("Rename playlist");
        rename.add_css_class("menu-action");
        rename.set_sensitive(editable);
        let delete = button("Delete playlist");
        delete.add_css_class("menu-action");
        delete.add_css_class("destructive");
        delete.set_sensitive(editable);
        choices.append(&rename);
        choices.append(&delete);
        options_popover.set_child(Some(&choices));
        let popover_weak = options_popover.downgrade();
        let weak = Rc::downgrade(pane);
        rename.connect_clicked(move |_| {
            close_popover(&popover_weak);
            if let Some(p) = weak.upgrade() {
                Self::open_rename(&p);
            }
        });
        let popover_weak = options_popover.downgrade();
        let weak = Rc::downgrade(pane);
        delete.connect_clicked(move |_| {
            close_popover(&popover_weak);
            if let Some(p) = weak.upgrade() {
                Self::confirm_delete(&p);
            }
        });
        options.set_popover(Some(&options_popover));
        actions.append(&options);
        info.append(&actions);
        hero.append(&info);
        self.songs.append(&hero);

        let songs_header = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
        songs_header.add_css_class("playlist-songs-header");
        songs_header.set_margin_top(22);
        songs_header.set_margin_bottom(4);
        songs_header.append(&label("Songs", "section-title"));
        songs_header.set_tooltip_text(Some("Type a song title or artist to jump. Repeat a letter to cycle; pause for one second to start a new word."));
        let hint = label(&count_label(entries.len()), "page-meta");
        hint.set_hexpand(true);
        hint.set_halign(gtk4::Align::End);
        songs_header.append(&hint);
        self.songs.append(&songs_header);
        if entries.is_empty() {
            self.songs.append(&label(
                "No songs in this playlist yet. Add songs from Search or the player bar.",
                "playlist-empty",
            ));
            return;
        }
        self.playlist_tracks = tracks;
        self.songs.append(&self.rows);
        self.append_rows(pane, detail, editable);
    }

    fn jump_to_song(&mut self, pane: &Rc<RefCell<Self>>, ch: char) {
        let Some(index) = self
            .type_jump
            .find(ch, Instant::now(), &self.playlist_tracks)
        else {
            self.status
                .set_text(&format!("No song matches ‘{}’.", self.type_jump.query));
            return;
        };
        let Some(detail) = self.state.detail.clone() else {
            return;
        };
        while self.rendered <= index {
            let previous = self.rendered;
            self.append_rows(
                pane,
                &detail,
                self.state.online && !self.busy && !self.state.pending,
            );
            if previous == self.rendered {
                return;
            }
        }
        let Some(row) = self.rows.first_child().and_then(|first| {
            let mut row = first;
            for _ in 0..index {
                row = row.next_sibling()?;
            }
            Some(row)
        }) else {
            return;
        };
        let title = &self.playlist_tracks[index].name;
        self.status
            .set_text(&format!("Jump: {} — {}", self.type_jump.query, title));
        let songs = self.songs.downgrade();
        let scroll = self.scroll.downgrade();
        // Let newly appended rows receive their layout before scrolling to the match.
        glib::timeout_add_local_once(Duration::from_millis(50), move || {
            if let (Some(songs), Some(scroll)) = (songs.upgrade(), scroll.upgrade()) {
                if let Some(bounds) = row.compute_bounds(&songs) {
                    if let Some(play) = row.first_child() {
                        play.grab_focus();
                    }
                    scroll
                        .vadjustment()
                        .clamp_page(bounds.y() as f64, (bounds.y() + bounds.height()) as f64);
                }
            }
        });
    }

    fn append_rows(&mut self, pane: &Rc<RefCell<Self>>, detail: &Playlist, editable: bool) {
        let entries = detail.entries.as_deref().unwrap_or_default();
        let end = (self.rendered + 100).min(entries.len());
        // shortcut: visited rows stay alive until the page refreshes; virtualize if 5000-song lists become costly.
        for (index, entry) in entries.iter().enumerate().take(end).skip(self.rendered) {
            let row = playlist_song_row(
                pane,
                &detail.id,
                detail.revision,
                entry,
                index,
                entries.len(),
                &self.playlist_tracks,
                editable,
                &self.artwork_loader,
                &self.tx,
            );
            self.rows.append(&row);
        }
        self.rendered = end;
    }

    fn render_overview(&mut self) {
        let playlists = self.state.playlists.clone();
        let header = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
        header.set_margin_top(8);
        header.set_margin_bottom(12);
        header.append(&label("Shared playlists", "section-title"));
        let count = label(
            &format!(
                "{0} {1}",
                playlists.len(),
                if playlists.len() == 1 {
                    "playlist"
                } else {
                    "playlists"
                }
            ),
            "page-meta",
        );
        count.set_hexpand(true);
        count.set_halign(gtk4::Align::End);
        header.append(&count);
        self.songs.append(&header);
        if playlists.is_empty() {
            self.songs.append(&label(
                "No shared playlists yet. Use the + beside Playlists in the sidebar to create one.",
                "playlist-empty",
            ));
            return;
        }
        let flow = gtk4::FlowBox::new();
        flow.add_css_class("playlist-flow");
        flow.set_selection_mode(gtk4::SelectionMode::None);
        flow.set_homogeneous(true);
        flow.set_min_children_per_line(2);
        flow.set_max_children_per_line(4);
        flow.set_column_spacing(18);
        flow.set_row_spacing(22);
        flow.set_valign(gtk4::Align::Start);
        for playlist in &playlists {
            let urls = self
                .cover_art
                .get(&playlist.id)
                .cloned()
                .unwrap_or_default();
            flow.insert(
                &playlist_card(playlist, &urls, &self.artwork_loader, &self.tx),
                -1,
            );
        }
        self.songs.append(&flow);
    }
    fn open_create(pane: &Rc<RefCell<Self>>) {
        let mut p = pane.borrow_mut();
        if p.busy {
            return;
        }
        if let Some((window, _)) = &p.popup {
            window.present();
            return;
        }
        let window = dialog_window(&p.parent, "New playlist", 380);
        close_on_escape(&window);
        let content = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
        content.set_margin_top(22);
        content.set_margin_bottom(18);
        content.set_margin_start(22);
        content.set_margin_end(22);
        content.append(&label("New playlist", "dialog-title"));
        let entry = gtk4::Entry::new();
        entry.set_placeholder_text(Some("Playlist name"));
        entry.set_max_length(120);
        entry.add_css_class("library-entry");
        content.append(&entry);
        let status = label("Names can be 1–120 characters.", "dialog-copy");
        status.set_wrap(true);
        content.append(&status);
        let footer = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        footer.set_halign(gtk4::Align::End);
        let weak = Rc::downgrade(pane);
        let cancel = close_button("Cancel", &window, move || {
            if let Some(p) = weak.upgrade() {
                p.borrow_mut().popup = None;
            }
        });
        cancel.add_css_class("library-secondary");
        let create = button("Create");
        create.add_css_class("accent");
        create.add_css_class("library-play");
        create.set_sensitive(false);
        footer.append(&cancel);
        footer.append(&create);
        content.append(&footer);
        window.set_child(Some(&content));
        let entry_changed = entry.clone();
        let create_changed = create.downgrade();
        entry_changed.connect_changed(move |entry| {
            if let Some(button) = create_changed.upgrade() {
                button.set_sensitive(valid_name(&entry.text()));
            }
        });
        let weak = Rc::downgrade(pane);
        let e = entry.clone();
        let s = status.clone();
        create.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::submit_create(&p, &e, &s);
            }
        });
        let weak = Rc::downgrade(pane);
        let s = status.clone();
        entry.connect_activate(move |e| {
            if let Some(p) = weak.upgrade() {
                Self::submit_create(&p, &e, &s);
            }
        });
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
        let focus = entry.clone();
        glib::idle_add_local_once(move || {
            focus.grab_focus();
        });
    }

    fn submit_create(pane: &Rc<RefCell<Self>>, entry: &gtk4::Entry, status: &gtk4::Label) {
        let name = entry.text().trim().to_string();
        if !valid_name(&name) {
            status.set_text("Enter a playlist name of 1–120 characters.");
            return;
        }
        let p = pane.borrow();
        if p.busy {
            status.set_text("Wait for the current library request to finish.");
            return;
        }
        if p.state.pending {
            status.set_text(
                "A save is pending. Close this dialog, then retry or discard it in Library.",
            );
            return;
        }
        drop(p);
        Self::request(pane, json!({"operation":"create","name":name}));
    }

    fn open_rename(pane: &Rc<RefCell<Self>>) {
        let mut p = pane.borrow_mut();
        if p.busy {
            return;
        }
        let Some(detail) = p.state.detail.clone() else {
            return;
        };
        if let Some((window, _)) = &p.popup {
            window.present();
            return;
        }
        let window = dialog_window(&p.parent, "Rename playlist", 380);
        close_on_escape(&window);
        let content = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
        content.set_margin_top(22);
        content.set_margin_bottom(18);
        content.set_margin_start(22);
        content.set_margin_end(22);
        content.append(&label("Rename playlist", "dialog-title"));
        let entry = gtk4::Entry::new();
        entry.set_text(&detail.name);
        entry.set_max_length(120);
        entry.add_css_class("library-entry");
        content.append(&entry);
        let status = label("Names can be 1–120 characters.", "dialog-copy");
        status.set_wrap(true);
        content.append(&status);
        let footer = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        footer.set_halign(gtk4::Align::End);
        let weak = Rc::downgrade(pane);
        let cancel = close_button("Cancel", &window, move || {
            if let Some(p) = weak.upgrade() {
                p.borrow_mut().popup = None;
            }
        });
        cancel.add_css_class("library-secondary");
        let rename = button("Rename");
        rename.add_css_class("accent");
        rename.add_css_class("library-play");
        rename.set_sensitive(valid_name(&detail.name));
        footer.append(&cancel);
        footer.append(&rename);
        content.append(&footer);
        window.set_child(Some(&content));
        let entry_changed = entry.clone();
        let rename_changed = rename.downgrade();
        entry_changed.connect_changed(move |entry| {
            if let Some(button) = rename_changed.upgrade() {
                button.set_sensitive(valid_name(&entry.text()));
            }
        });
        let weak = Rc::downgrade(pane);
        let e = entry.clone();
        let s = status.clone();
        let id = detail.id.clone();
        let revision = detail.revision;
        rename.connect_clicked(move |_| {
            if let Some(p) = weak.upgrade() {
                Self::submit_rename(&p, &e, &s, &id, revision);
            }
        });
        let weak = Rc::downgrade(pane);
        let s = status.clone();
        let id = detail.id.clone();
        let revision = detail.revision;
        entry.connect_activate(move |e| {
            if let Some(p) = weak.upgrade() {
                Self::submit_rename(&p, &e, &s, &id, revision);
            }
        });
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
        let focus = entry.clone();
        glib::idle_add_local_once(move || {
            focus.grab_focus();
            focus.select_region(0, -1);
        });
    }

    fn submit_rename(
        pane: &Rc<RefCell<Self>>,
        entry: &gtk4::Entry,
        status: &gtk4::Label,
        id: &str,
        revision: u64,
    ) {
        let name = entry.text().trim().to_string();
        if !valid_name(&name) {
            status.set_text("Enter a playlist name of 1–120 characters.");
            return;
        }
        let p = pane.borrow();
        if p.busy {
            status.set_text("Wait for the current library request to finish.");
            return;
        }
        if p.state.pending {
            status.set_text(
                "A save is pending. Close this dialog, then retry or discard it in Library.",
            );
            return;
        }
        drop(p);
        Self::request(
            pane,
            json!({"operation":"rename","id":id,"revision":revision,"name":name}),
        );
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
        let window = dialog_window(&p.parent, "Add to playlist", 430);
        close_on_escape(&window);
        let content = gtk4::Box::new(gtk4::Orientation::Vertical, 10);
        content.set_margin_top(20);
        content.set_margin_bottom(18);
        content.set_margin_start(20);
        content.set_margin_end(20);
        content.append(&label("Add to playlist", "dialog-title"));
        let song = label(&track.name, "dialog-song-title");
        song.set_hexpand(true);
        ellipsized(&song, 48);
        content.append(&song);
        let artist = label(&track.artist_line(), "dialog-copy");
        artist.set_hexpand(true);
        ellipsized(&artist, 48);
        content.append(&artist);
        let status = label(
            if !p.state.online {
                "Library is read-only. Refresh after reconnecting."
            } else if p.state.pending {
                "A save is pending. Retry or discard it in Library first."
            } else if p.state.playlists.is_empty() {
                "No playlists yet. Create one in Library first."
            } else {
                "Choose a playlist for this song."
            },
            "dialog-copy",
        );
        status.set_wrap(true);
        content.append(&status);
        let scroll = gtk4::ScrolledWindow::new();
        scroll.set_vexpand(true);
        scroll.set_hscrollbar_policy(gtk4::PolicyType::Never);
        let visible_rows = p.state.playlists.len().min(3) as i32;
        scroll.set_min_content_height(if visible_rows == 0 {
            0
        } else {
            visible_rows * 48 - 4
        });
        scroll.set_max_content_height(220);
        let lists = gtk4::Box::new(gtk4::Orientation::Vertical, 4);
        for playlist in &p.state.playlists {
            let b = gtk4::Button::new();
            b.add_css_class("dialog-playlist-row");
            b.set_hexpand(true);
            b.set_halign(gtk4::Align::Fill);
            b.set_sensitive(p.state.online && !p.state.pending && playlist.track_count < 5000);
            let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
            let icon = gtk4::Image::from_icon_name("folder-music-symbolic");
            icon.set_pixel_size(14);
            let name = label(&playlist.name, "playlist-name");
            name.set_hexpand(true);
            ellipsized(&name, 32);
            let count = label(&count_label(playlist.track_count), "playlist-count");
            row.append(&icon);
            row.append(&name);
            row.append(&count);
            b.set_child(Some(&row));
            b.update_property(&[gtk4::accessible::Property::Label(&format!(
                "Add to {}, {}",
                playlist.name,
                count_label(playlist.track_count)
            ))]);
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
        let footer = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        footer.set_halign(gtk4::Align::End);
        let weak = Rc::downgrade(pane);
        let refresh = close_button("Refresh list", &window, move || {
            if let Some(p) = weak.upgrade() {
                p.borrow_mut().popup = None;
                Self::request(&p, json!({"operation":"refresh"}));
            }
        });
        refresh.add_css_class("library-secondary");
        let weak = Rc::downgrade(pane);
        let close = close_button("Close", &window, move || {
            if let Some(p) = weak.upgrade() {
                p.borrow_mut().popup = None;
            }
        });
        close.add_css_class("library-secondary");
        footer.append(&refresh);
        footer.append(&close);
        content.append(&footer);
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
        serde_json::from_value(json!({"playlists":[
            {"id":"11111111-1111-4111-8111-111111111111","name":"GG","revision":1,"trackCount":2},
            {"id":"44444444-4444-4444-8444-444444444444","name":"Other","revision":1,"trackCount":0}
        ],
          "detail":{"id":"11111111-1111-4111-8111-111111111111","name":"GG","revision":1,"trackCount":2,"entries":[
            {"id":"22222222-2222-4222-8222-222222222222","track":{"id":"dm4tkSNKfFI","title":"Awake","artist":"Tycho","durationMs":284000}},
            {"id":"33333333-3333-4333-8333-333333333333","track":{"id":"dm4tkSNKfFI","title":"Awake","artist":"Tycho","durationMs":284000}}]},
          "online":true,"pending":false})).unwrap()
    }
    #[test]
    fn type_jump_matches_words_cycles_and_resets() {
        let template = state().detail.unwrap().entries.unwrap()[0]
            .track
            .playable()
            .unwrap();
        let tracks: Vec<Track> = ["Other Zebra", "Seven Years", "Saosin Song", "Élan"]
            .into_iter()
            .map(|name| Track {
                name: name.into(),
                ..template.clone()
            })
            .collect();
        let now = Instant::now();
        let mut jump = TypeJump::default();
        assert_eq!(jump.find('S', now, &tracks), Some(1));
        assert_eq!(jump.find('s', now, &tracks), Some(2));
        assert_eq!(jump.find('s', now, &tracks), Some(1));
        assert_eq!(jump.find('e', now, &tracks), Some(1));
        assert_eq!(jump.query, "se");
        assert_eq!(
            jump.find('z', now + Duration::from_secs(2), &tracks),
            Some(0)
        );
        assert_eq!(
            jump.find('é', now + Duration::from_secs(4), &tracks),
            Some(3)
        );
        assert_eq!(jump.find('x', now + Duration::from_secs(6), &tracks), None);
        assert_eq!(TypeJump::default().find('a', now, &[]), None);
        assert_eq!(TypeJump::default().find('t', now, &tracks), Some(0)); // artist Tycho
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
    #[test]
    fn shuffle_preserves_recordings_duplicates_and_original_order() {
        assert!(shuffled_tracks(&[]).is_empty());
        let base = state().detail.unwrap().entries.unwrap()[0]
            .track
            .playable()
            .unwrap();
        assert_eq!(
            shuffled_tracks(std::slice::from_ref(&base)),
            vec![base.clone()]
        );
        let mut tracks: Vec<Track> = (0..20)
            .map(|index| {
                let mut track = base.clone();
                track.name = format!("Song {}", index / 2);
                track
            })
            .collect();
        let original = tracks.clone();
        let mut shuffled = shuffled_tracks(&tracks);
        assert_eq!(tracks, original);
        tracks.sort_by(|a, b| a.name.cmp(&b.name));
        shuffled.sort_by(|a, b| a.name.cmp(&b.name));
        assert_eq!(shuffled, tracks);
    }

    #[test]
    fn playlist_names_are_trimmed_and_bounded() {
        assert!(valid_name("  Night drive  "));
        assert!(!valid_name("   "));
        assert!(!valid_name(&"x".repeat(121)));
        assert!(!valid_name("bad\nname"));
    }
    #[test]
    fn cover_art_is_deduplicated_and_bounded() {
        let mut entries = state().validate().unwrap().detail.unwrap().entries.unwrap();
        for index in 0..6 {
            entries[0].track.artwork_url = Some(format!("https://i.ytimg.com/{index}.jpg"));
            entries.push(entries[0].clone());
        }
        let urls = cover_urls(&entries);
        assert_eq!(urls.len(), MAX_COVER_ART);
        assert_eq!(urls.iter().collect::<HashSet<_>>().len(), MAX_COVER_ART);
    }
    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn focus_refresh_does_not_interrupt_playlist_menu() {
        gtk4::init().expect("GTK display");
        let provider = gtk4::CssProvider::new();
        provider.load_from_string(include_str!("style.css"));
        gtk4::style_context_add_provider_for_display(
            &gdk::Display::default().unwrap(),
            &provider,
            gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
        let application = gtk4::Application::new(None::<&str>, Default::default());
        application
            .register(None::<&gtk4::gio::Cancellable>)
            .unwrap();
        let parent = gtk4::ApplicationWindow::new(&application);
        parent.set_default_size(1180, 760);
        let (tx, rx) = mpsc::channel();
        let pane = LibraryPane::new(
            &parent,
            tx,
            Rc::new(RefCell::new(ArtworkLoader::default())),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
        );
        parent.set_child(Some(&pane.borrow().page));
        LibraryPane::apply(&pane, Ok(state()));
        parent.present();
        fn settle() {
            let until = std::time::Instant::now() + std::time::Duration::from_millis(200);
            while std::time::Instant::now() < until {
                while glib::MainContext::default().pending() {
                    glib::MainContext::default().iteration(false);
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        }
        fn options(widget: &gtk4::Widget) -> Option<gtk4::MenuButton> {
            if widget.has_css_class("playlist-options") {
                return widget.clone().downcast().ok();
            }
            let mut child = widget.first_child();
            while let Some(current) = child {
                if let Some(found) = options(&current) {
                    return Some(found);
                }
                child = current.next_sibling();
            }
            None
        }
        settle();
        assert!(parent.is_active(), "native test window did not gain focus");
        let menu = options(pane.borrow().page.upcast_ref()).unwrap();
        // An activating mouse click schedules refresh before GTK opens the menu.
        LibraryPane::refresh_on_focus(&pane);
        menu.popup();
        settle();
        assert!(menu.is_active());
        assert!(!pane.borrow().busy);
        assert!(rx.try_recv().is_err(), "opening the menu must not refresh");
        let surface = menu.popover().unwrap().surface().unwrap();
        assert!(
            surface.width() <= 180 && surface.height() <= 112,
            "playlist popup is oversized: {}x{}",
            surface.width(),
            surface.height()
        );
        let choices = menu.popover().unwrap().child().unwrap();
        let rename = choices
            .first_child()
            .unwrap()
            .downcast::<gtk4::Button>()
            .unwrap();
        assert_eq!(rename.label().as_deref(), Some("Rename playlist"));
        let delete = rename
            .next_sibling()
            .unwrap()
            .downcast::<gtk4::Button>()
            .unwrap();
        assert_eq!(delete.label().as_deref(), Some("Delete playlist"));
        rename.emit_clicked();
        settle();
        let window = pane.borrow().popup.as_ref().unwrap().0.clone();
        assert_eq!(window.title().as_deref(), Some("Rename playlist"));
        LibraryPane::refresh_on_focus(&pane);
        settle();
        assert!(rx.try_recv().is_err(), "dialog must not trigger refresh");
        window.close();
        parent.present();
        settle();
        LibraryPane::refresh_on_focus(&pane);
        settle();
        let Action::Request(command) = rx.try_recv().unwrap() else {
            panic!("expected refresh");
        };
        assert_eq!(command["operation"], "refresh");
        parent.close();
    }

    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn scrolling_appends_batches_without_replacing_rows() {
        gtk4::init().expect("GTK display");
        let application = gtk4::Application::new(None::<&str>, Default::default());
        application
            .register(None::<&gtk4::gio::Cancellable>)
            .unwrap();
        let parent = gtk4::ApplicationWindow::new(&application);
        parent.set_default_size(1180, 760);
        let (tx, rx) = mpsc::channel();
        let pane = LibraryPane::new(
            &parent,
            tx,
            Rc::new(RefCell::new(ArtworkLoader::default())),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
        );
        parent.set_child(Some(&pane.borrow().page));
        let mut large = state();
        let template = large.detail.as_ref().unwrap().entries.as_ref().unwrap()[0].clone();
        let entries: Vec<Entry> = (0..205)
            .map(|index| Entry {
                id: format!("{index:08x}-2222-4222-8222-222222222222"),
                track: template.track.clone(),
            })
            .collect();
        large.detail.as_mut().unwrap().entries = Some(entries);
        large.detail.as_mut().unwrap().track_count = 205;
        large.playlists[0].track_count = 205;
        LibraryPane::apply(&pane, Ok(large.validate().unwrap()));
        parent.present();
        fn settle() {
            let until = std::time::Instant::now() + std::time::Duration::from_millis(150);
            while std::time::Instant::now() < until {
                while glib::MainContext::default().pending() {
                    glib::MainContext::default().iteration(false);
                }
                std::thread::sleep(std::time::Duration::from_millis(5));
            }
        }
        settle();
        assert_eq!(pane.borrow().rendered, 100);
        fn find_shuffle(widget: &gtk4::Widget) -> Option<gtk4::Button> {
            if widget.has_css_class("playlist-shuffle") {
                return widget.clone().downcast().ok();
            }
            let mut child = widget.first_child();
            while let Some(current) = child {
                if let Some(button) = find_shuffle(&current) {
                    return Some(button);
                }
                child = current.next_sibling();
            }
            None
        }
        let shuffle = find_shuffle(pane.borrow().page.upcast_ref()).unwrap();
        shuffle.emit_clicked();
        let Action::Play(tracks, index) = rx.try_recv().unwrap() else {
            panic!("expected shuffled Play");
        };
        assert_eq!((tracks.len(), index), (205, 0));
        let first = pane.borrow().rows.first_child().unwrap();
        let adjustment = pane.borrow().scroll.vadjustment();
        for expected in [200, 205] {
            adjustment.set_value(adjustment.upper() - adjustment.page_size() - 200.0);
            let position = adjustment.value();
            settle();
            assert_eq!(pane.borrow().rendered, expected);
            assert_eq!(pane.borrow().rows.first_child().unwrap(), first);
            assert!(
                (adjustment.value() - position).abs() < 1.0,
                "scroll position jumped"
            );
        }
        let mut row = first.clone();
        for _ in 0..100 {
            row = row.next_sibling().unwrap();
        }
        row.first_child()
            .unwrap()
            .downcast::<gtk4::Button>()
            .unwrap()
            .emit_clicked();
        let Action::Play(tracks, index) = rx.try_recv().unwrap() else {
            panic!("expected Play");
        };
        assert_eq!((tracks.len(), index), (205, 100));
        adjustment.set_value(adjustment.upper());
        settle();
        assert_eq!(pane.borrow().rendered, 205);
        LibraryPane::apply(&pane, Ok(state()));
        settle();
        assert_eq!(pane.borrow().rendered, 2);
        let mut large = state();
        let template = large.detail.as_ref().unwrap().entries.as_ref().unwrap()[0].clone();
        let entries: Vec<Entry> = (0..205)
            .map(|index| {
                let mut entry = template.clone();
                entry.id = format!("{index:08x}-2222-4222-8222-222222222222");
                if index == 150 {
                    entry.track.title = "Zebra".into();
                }
                entry
            })
            .collect();
        large.detail.as_mut().unwrap().entries = Some(entries);
        large.detail.as_mut().unwrap().track_count = 205;
        large.playlists[0].track_count = 205;
        LibraryPane::apply(&pane, Ok(large.validate().unwrap()));
        settle();
        assert_eq!(pane.borrow().rendered, 100);
        let controllers = parent.observe_controllers();
        let keys = (0..controllers.n_items())
            .find_map(|i| {
                controllers
                    .item(i)?
                    .downcast::<gtk4::EventControllerKey>()
                    .ok()
            })
            .unwrap();
        let handled = keys.emit_by_name::<bool>(
            "key-pressed",
            &[&gdk::Key::z, &0u32, &gdk::ModifierType::empty()],
        );
        assert!(handled);
        settle();
        assert!(pane.borrow().rendered >= 151);
        assert!(pane.borrow().scroll.vadjustment().value() > 0.0);
        let focus = gtk4::prelude::GtkWindowExt::focus(&parent).unwrap();
        assert_eq!(focus.tooltip_text().as_deref(), Some("Play Zebra"));
        assert!(rx.try_recv().is_err(), "jump must not start playback");
        parent.close();
    }

    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn closed_dialog_callbacks_release_window() {
        gtk4::init().expect("GTK display");
        let window = dialog_window(&glib::WeakRef::new(), "Lifecycle test", 240);
        let weak = window.downgrade();
        close_on_escape(&window);
        let close = close_button("Close", &window, || {});
        window.set_child(Some(&close));
        window.present();
        let context = glib::MainContext::default();
        for _ in 0..5 {
            while context.pending() {
                context.iteration(false);
            }
            std::thread::sleep(std::time::Duration::from_millis(10));
        }
        close.emit_by_name::<()>("clicked", &[]);
        while context.pending() {
            context.iteration(false);
        }
        drop(close);
        drop(window);
        assert!(
            weak.upgrade().is_none(),
            "closed dialog remained retained by its callbacks"
        );
        let application = gtk4::Application::new(None::<&str>, Default::default());
        application
            .register(None::<&gtk4::gio::Cancellable>)
            .unwrap();
        let parent = gtk4::ApplicationWindow::new(&application);
        let (tx, _rx) = mpsc::channel();
        let pane = LibraryPane::new(
            &parent,
            tx,
            Rc::new(RefCell::new(ArtworkLoader::default())),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
            &gtk4::Box::new(gtk4::Orientation::Vertical, 0),
        );
        LibraryPane::apply(&pane, Ok(state()));
        fn descendants(widget: &gtk4::Widget, refs: &mut Vec<glib::WeakRef<gtk4::Widget>>) {
            refs.push(widget.downgrade());
            let mut child = widget.first_child();
            while let Some(current) = child {
                descendants(&current, refs);
                child = current.next_sibling();
            }
        }
        for dialog in 0..3 {
            match dialog {
                0 => LibraryPane::open_create(&pane),
                1 => LibraryPane::open_rename(&pane),
                _ => LibraryPane::add_dialog(
                    &pane,
                    &state().detail.unwrap().entries.unwrap()[0]
                        .track
                        .playable()
                        .unwrap(),
                ),
            }
            while context.pending() {
                context.iteration(false);
            }
            let window = pane.borrow().popup.as_ref().unwrap().0.clone();
            let mut refs = Vec::new();
            descendants(window.upcast_ref(), &mut refs);
            window.close();
            drop(window);
            while context.pending() {
                context.iteration(false);
            }
            assert!(
                refs.iter().all(|r| r.upgrade().is_none()),
                "dialog {dialog} retained controls"
            );
        }
        let mut populated = state();
        populated.detail.as_mut().unwrap().entries.as_mut().unwrap()[0]
            .track
            .artwork_url = Some("https://i.ytimg.com/vi/dm4tkSNKfFI/default.jpg".into());
        let id = populated.detail.as_ref().unwrap().id.clone();
        LibraryPane::apply(&pane, Ok(populated.clone()));
        assert!(pane.borrow().cover_art.contains_key(&id));
        let mut empty = populated.clone();
        empty.detail.as_mut().unwrap().entries = Some(vec![]);
        empty.detail.as_mut().unwrap().track_count = 0;
        empty.playlists[0].track_count = 0;
        LibraryPane::apply(&pane, Ok(empty));
        assert!(!pane.borrow().cover_art.contains_key(&id));
        LibraryPane::apply(&pane, Ok(populated.clone()));
        let mut deleted = state();
        deleted.detail = None;
        deleted.playlists.remove(0);
        LibraryPane::apply(&pane, Ok(deleted));
        assert!(!pane.borrow().cover_art.contains_key(&id));
        LibraryPane::apply(&pane, Ok(populated));
        LibraryPane::reset(&pane);
        assert!(pane.borrow().cover_art.is_empty());
    }
}
