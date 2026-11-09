use crate::artwork::{ArtworkLoader, ArtworkSlot, artwork_view};
use crate::bridge::{BridgePayload, BridgeService, JobKind, JobResult, normalize_query};
use crate::config::BridgeConfig;
use crate::lyrics::{LyricsData, LyricsKind};
use crate::model::{ResolveData, Track};
use crate::playback::{PauseAction, PlayAction, PlaybackState, ResolveDisposition};
use crate::player::{Player, PlayerEvent, PlayerEventKind};
use crate::queue::{EntryId, Queue, QueueEntry};
use gtk4::prelude::*;
use gtk4::{gdk, glib, pango};
use std::cell::{Cell, RefCell};
use std::rc::{Rc, Weak};
use std::sync::mpsc;
use std::time::{Duration, Instant};

const DEFAULT_VOLUME: f64 = 0.7;
const SIDEBAR_WIDTH: i32 = 204;
const SIDE_PANEL_WIDTH: i32 = 340;
const HOME_CARD_GAP: u32 = 18;
const HOME_CARD_MIN_WIDTH: i32 = 150;
const PROGRESS_MAX: f64 = 1000.0;

mod square_artwork {
    use gtk4::subclass::prelude::*;
    use gtk4::{glib, prelude::*};

    #[derive(Default)]
    pub struct SquareArtwork;

    #[glib::object_subclass]
    impl ObjectSubclass for SquareArtwork {
        const NAME: &'static str = "EmberSquareArtwork";
        type Type = super::SquareArtwork;
        type ParentType = gtk4::Widget;
    }
    impl ObjectImpl for SquareArtwork {
        fn dispose(&self) {
            while let Some(child) = self.obj().first_child() {
                child.unparent();
            }
        }
    }
    impl WidgetImpl for SquareArtwork {
        fn request_mode(&self) -> gtk4::SizeRequestMode {
            gtk4::SizeRequestMode::HeightForWidth
        }
        fn measure(&self, orientation: gtk4::Orientation, for_size: i32) -> (i32, i32, i32, i32) {
            if orientation == gtk4::Orientation::Vertical && for_size >= 0 {
                (for_size, for_size, -1, -1)
            } else {
                (
                    super::HOME_CARD_MIN_WIDTH,
                    super::HOME_CARD_MIN_WIDTH,
                    -1,
                    -1,
                )
            }
        }
        fn size_allocate(&self, width: i32, _height: i32, baseline: i32) {
            if let Some(child) = self.obj().first_child() {
                child.allocate(width, width, baseline, None);
            }
        }
        fn snapshot(&self, snapshot: &gtk4::Snapshot) {
            if let Some(child) = self.obj().first_child() {
                self.obj().snapshot_child(&child, snapshot);
            }
        }
    }
}

glib::wrapper! {
    pub struct SquareArtwork(ObjectSubclass<square_artwork::SquareArtwork>)
        @extends gtk4::Widget,
        @implements gtk4::Accessible, gtk4::Buildable, gtk4::ConstraintTarget;
}

pub fn run() -> glib::ExitCode {
    let application = gtk4::Application::builder()
        .application_id("io.ember.RustPreview")
        .build();
    application.connect_activate(activate);
    application.run()
}

fn activate(application: &gtk4::Application) {
    install_css();
    if let Some(display) = gdk::Display::default() {
        crate::icons::install(&display);
    }

    let window = gtk4::ApplicationWindow::builder()
        .application(application)
        .title("Ember")
        .default_width(1180)
        .default_height(760)
        .build();
    window.set_size_request(780, 580);

    let sidebar = build_sidebar();
    let settings = build_settings_window(&window);
    let settings_window = settings.0.clone();
    sidebar.settings_button.connect_clicked(move |_| {
        settings_window.present();
    });
    let (library_tx, library_rx) = mpsc::channel();
    let library = crate::library::LibraryPane::new(&window, library_tx);
    let home = build_home_page();
    let search = build_search_page();
    let player = build_player_bar();
    let add_playing = icon_button("list-add-symbolic", "Add current song to playlist");
    add_playing.add_css_class("top-action");
    player.header.pack_end(&add_playing);

    let setup_label = label("", "setup-banner");
    setup_label.set_wrap(true);
    setup_label.set_wrap_mode(pango::WrapMode::WordChar);
    setup_label.set_visible(false);
    setup_label.set_selectable(true);
    setup_label.set_margin_top(12);
    setup_label.set_margin_start(24);
    setup_label.set_margin_end(24);

    let main_stack = gtk4::Stack::new();
    main_stack.set_hexpand(true);
    main_stack.set_vexpand(true);
    main_stack.set_transition_type(gtk4::StackTransitionType::Crossfade);
    main_stack.set_transition_duration(120);
    main_stack.add_named(&home.page, Some("home"));
    main_stack.add_named(&library.borrow().page, Some("library"));
    main_stack.add_named(&search.page, Some("search"));
    main_stack.set_visible_child_name("home");

    let status_row = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    status_row.add_css_class("status-strip");
    status_row.set_margin_start(24);
    status_row.set_margin_end(24);
    status_row.set_margin_top(5);
    status_row.set_margin_bottom(7);
    let spinner = gtk4::Spinner::new();
    spinner.set_visible(false);
    spinner.set_size_request(14, 14);
    let status_label = label("Ready.", "status-line");
    status_label.set_hexpand(true);
    status_label.set_ellipsize(pango::EllipsizeMode::End);
    status_label.set_max_width_chars(96);
    status_row.append(&spinner);
    status_row.append(&status_label);

    let main = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    main.add_css_class("main-view");
    main.set_hexpand(true);
    main.set_vexpand(true);
    main.append(&setup_label);
    main.append(&main_stack);
    main.append(&status_row);

    let body = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    body.set_hexpand(true);
    body.set_vexpand(true);
    body.append(&sidebar.container);
    body.append(&main);

    let body_overlay = gtk4::Overlay::new();
    body_overlay.set_child(Some(&body));
    let side_panel = build_side_panel();
    side_panel.container.set_halign(gtk4::Align::End);
    side_panel.container.set_valign(gtk4::Align::Fill);
    side_panel.container.set_margin_top(10);
    side_panel.container.set_margin_bottom(10);
    side_panel.container.set_margin_end(10);
    body_overlay.add_overlay(&side_panel.container);
    window.set_child(Some(&body_overlay));
    window.set_titlebar(Some(&player.header));

    let (tx, rx) = mpsc::channel();
    let (player_tx, player_rx) = mpsc::channel();
    let mut setup_messages = Vec::new();
    let bridge = match BridgeConfig::discover() {
        Ok(config) => match BridgeService::new(config, tx) {
            Ok(service) => Some(service),
            Err(error) => {
                setup_messages.push(error.message().to_owned());
                None
            }
        },
        Err(error) => {
            setup_messages.push(error.to_string());
            None
        }
    };
    let player_ready = match Player::check_available() {
        Ok(()) => true,
        Err(error) => {
            setup_messages.push(error);
            false
        }
    };

    let setup_failed = !setup_messages.is_empty();
    if setup_failed {
        setup_label.set_text(&setup_messages.join("\n\n"));
        setup_label.set_visible(true);
        status_label.set_text("Setup needs attention.");
        status_label.add_css_class("error");
    } else {
        status_label.set_text("Ready.");
    }
    sidebar.search_entry.set_sensitive(bridge.is_some());
    sidebar.search_button.set_sensitive(bridge.is_some());
    player.play_button.set_sensitive(false);
    player.stop_button.set_sensitive(false);
    player.previous_button.set_sensitive(false);
    player.next_button.set_sensitive(false);
    player.queue_toggle.set_sensitive(true);
    player.lyrics_toggle.set_sensitive(true);
    player.volume_scale.set_sensitive(player_ready);
    player.progress_scale.set_sensitive(false);

    let progress_suppress = Rc::new(Cell::new(false));
    let artwork_loader = Rc::new(RefCell::new(ArtworkLoader::default()));
    let ui = Rc::new(RefCell::new(AppUi {
        window: window.clone(),
        search_entry: sidebar.search_entry,
        search_button: sidebar.search_button,
        spinner,
        status_label,
        main_stack,
        library: library.clone(),
        library_seq: 0,
        nav_library: sidebar.library_button,
        nav_home: sidebar.home_button,
        nav_search: sidebar.search_button_nav,
        nav_queue: sidebar.queue_button,
        queue_nav_count: sidebar.queue_count,
        home_stack: home.stack,
        home_search_button: home.search_button,
        recent_flow: home.recent_flow,
        recent_count: home.recent_count,
        empty_label: search.empty_label,
        results_hint: search.results_hint,
        results_stack: search.results_stack,
        results_list: search.results_list,
        title_label: player.title,
        artist_label: player.artist,
        play_icon: player.play_icon,
        play_button: player.play_button,
        stop_button: player.stop_button,
        previous_button: player.previous_button,
        next_button: player.next_button,
        volume_scale: player.volume_scale,
        volume: DEFAULT_VOLUME * 100.0,
        progress_scale: player.progress_scale,
        progress_time: player.progress_time,
        progress_suppress,
        player_art: player.artwork,
        artwork_loader,
        tracks: Vec::new(),
        recent_tracks: Vec::new(),
        bridge,
        player_tx,
        player: None,
        player_ready,
        queue: Queue::default(),
        playback: PlaybackState::default(),
        prepared: None,
        cached_playback: None,
        search_seq: 0,
        resolve_seq: 0,
        side_panel: side_panel.container,
        side_close: side_panel.close,
        side_title: side_panel.title,
        side_stack: side_panel.stack,
        queue_toggle: player.queue_toggle,
        lyrics_toggle: player.lyrics_toggle,
        queue_current_title: side_panel.queue_current_title,
        queue_current_artist: side_panel.queue_current_artist,
        queue_current_duration: side_panel.queue_current_duration,
        queue_list: side_panel.queue_list,
        queue_stack: side_panel.queue_stack,
        queue_count: side_panel.queue_count,
        queue_clear: side_panel.queue_clear,
        lyrics_stack: side_panel.lyrics_stack,
        lyrics_spinner: side_panel.lyrics_spinner,
        lyrics_message: side_panel.lyrics_message,
        lyrics_retry: side_panel.lyrics_retry,
        lyrics_scroll: side_panel.lyrics_scroll,
        lyrics_lines: side_panel.lyrics_lines,
        lyrics_follow: side_panel.lyrics_follow,
        lyrics_attribution: side_panel.lyrics_attribution,
        lyrics_data: None,
        lyrics_loading: false,
        lyrics_seq: 0,
        lyrics_track_id: None,
        lyrics_following: true,
        lyrics_active: None,
        lyrics_line_labels: Vec::new(),
        lyrics_line_widgets: Vec::new(),
        lyrics_suppress_scroll: Rc::new(Cell::new(false)),
        lyrics_scroll_animation: None,
        self_weak: Weak::new(),
        closing: false,
    }));
    ui.borrow_mut().self_weak = Rc::downgrade(&ui);
    ui.borrow_mut().refresh_home();
    ui.borrow_mut().refresh_queue_view();
    ui.borrow_mut().show_lyrics_idle();
    ui.borrow_mut().sync_nav();
    ui.borrow_mut().refresh_transport();
    ui.borrow_mut().refresh_progress();

    let weak_ui = Rc::downgrade(&ui);
    settings.1.connect_toggled(move |toggle| {
        let Some(ui) = weak_ui.upgrade() else {
            return;
        };
        let mut ui = ui.borrow_mut();
        if crate::cache::set_enabled(toggle.is_active()).is_err() {
            ui.set_status(
                "Could not save cache settings. Check disk space and permissions.",
                true,
            );
            return;
        }
        if !toggle.is_active() {
            if let Some(bridge) = &ui.bridge {
                bridge.cancel_cache();
            }
        }
    });
    let weak_ui = Rc::downgrade(&ui);
    settings.2.connect_clicked(move |_| {
        let Some(ui) = weak_ui.upgrade() else {
            return;
        };
        let mut ui = ui.borrow_mut();
        if let Some(bridge) = &ui.bridge {
            bridge.clear_cache();
            ui.set_status("Clearing audio cache…", false);
        }
    });
    let weak_ui = Rc::downgrade(&ui);
    add_playing.connect_clicked(move |_| {
        if let Some(ui) = weak_ui.upgrade() {
            let ui = ui.borrow();
            if let Some(entry) = ui.queue.current() {
                crate::library::LibraryPane::add_dialog(&ui.library, &entry.track);
            }
        }
    });
    let (server, key) = crate::library::connection_fields();
    settings.3.set_text(&server);
    settings.4.set_text(&key);
    let server_field = settings.3.clone();
    let key_field = settings.4.clone();
    let feedback = settings.6.clone();
    let pane = library.clone();
    settings.5.connect_clicked(move |_| {
        if pane.borrow().busy {
            feedback.set_text("Wait for the current library request to finish.");
            return;
        }
        match crate::library::save_connection(&server_field.text(), &key_field.text()) {
            Ok(()) => {
                feedback.set_text("Saved privately. Connecting to shared playlists…");
                crate::library::LibraryPane::reset(&pane);
            }
            Err(message) => feedback.set_text(&message),
        }
    });
    install_callbacks(&ui, rx, player_rx, library_rx);
    crate::library::LibraryPane::request(&library, serde_json::json!({"operation":"refresh"}));
    ui.borrow().window.present();
    ui.borrow().search_entry.grab_focus();
}

fn build_settings_window(
    parent: &gtk4::ApplicationWindow,
) -> (
    gtk4::Window,
    gtk4::CheckButton,
    gtk4::Button,
    gtk4::Entry,
    gtk4::PasswordEntry,
    gtk4::Button,
    gtk4::Label,
) {
    let window = gtk4::Window::builder()
        .title("Ember Settings")
        .transient_for(parent)
        .modal(true)
        .hide_on_close(true)
        .default_width(420)
        .resizable(false)
        .build();
    let content = gtk4::Box::new(gtk4::Orientation::Vertical, 16);
    content.set_margin_top(24);
    content.set_margin_bottom(24);
    content.set_margin_start(24);
    content.set_margin_end(24);
    content.append(&label("Audio cache", "page-title"));
    let enabled = gtk4::CheckButton::with_label("Automatically cache played songs");
    enabled.set_active(crate::cache::enabled());
    content.append(&enabled);
    let explanation = label(
        "Repeat plays use local audio. The 2 GiB cache removes least recently used songs automatically. New songs stream immediately while a separate download saves a copy.",
        "secondary",
    );
    explanation.set_wrap(true);
    explanation.set_max_width_chars(45);
    content.append(&explanation);
    let clear = gtk4::Button::with_label("Clear audio cache");
    content.append(&clear);
    content.append(&label("Shared playlists", "section-title"));
    let server = gtk4::Entry::new();
    server.set_placeholder_text(Some("https://your-server/ember"));
    server.set_max_length(2048);
    server.set_tooltip_text(Some("Same Ember server address as your iPhone"));
    content.append(&server);
    let key = gtk4::PasswordEntry::new();
    key.set_placeholder_text(Some("Ember access key"));
    key.set_show_peek_icon(true);
    content.append(&key);
    let save = gtk4::Button::with_label("Save playlist connection");
    content.append(&save);
    let feedback = label(
        "Access key is stored in an owner-only file on this PC.",
        "secondary",
    );
    feedback.set_wrap(true);
    feedback.set_max_width_chars(45);
    content.append(&feedback);
    let close = gtk4::Button::with_label("Done");
    let weak_window = window.downgrade();
    close.connect_clicked(move |_| {
        if let Some(window) = weak_window.upgrade() {
            window.close();
        }
    });
    content.append(&close);
    window.set_child(Some(&content));
    (window, enabled, clear, server, key, save, feedback)
}

struct SidebarParts {
    settings_button: gtk4::Button,
    library_button: gtk4::Button,
    container: gtk4::Box,
    search_entry: gtk4::Entry,
    search_button: gtk4::Button,
    home_button: gtk4::Button,
    search_button_nav: gtk4::Button,
    queue_button: gtk4::Button,
    queue_count: gtk4::Label,
}

fn build_sidebar() -> SidebarParts {
    let container = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    container.add_css_class("sidebar");
    container.set_size_request(SIDEBAR_WIDTH, -1);
    container.set_hexpand(false);
    container.set_halign(gtk4::Align::Start);
    container.set_vexpand(true);

    let brand_row = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    brand_row.add_css_class("brand-row");
    let brand_mark = gtk4::Image::from_icon_name("media-optical-symbolic");
    brand_mark.set_pixel_size(17);
    brand_mark.add_css_class("brand-mark");
    brand_row.append(&brand_mark);
    brand_row.append(&label("Ember", "brand"));
    let settings_button = gtk4::Button::new();
    settings_button.add_css_class("brand-button");
    settings_button.set_child(Some(&brand_row));
    settings_button.set_tooltip_text(Some("Open Ember settings"));
    settings_button.update_property(&[gtk4::accessible::Property::Label("Open Ember settings")]);
    container.append(&settings_button);

    let search_row = gtk4::Box::new(gtk4::Orientation::Horizontal, 5);
    search_row.add_css_class("sidebar-search");
    search_row.set_margin_start(12);
    search_row.set_margin_end(12);
    search_row.set_margin_top(2);
    search_row.set_margin_bottom(8);
    let search_button = icon_button("system-search-symbolic", "Search music");
    search_button.add_css_class("search-submit");
    let search_entry = gtk4::Entry::new();
    search_entry.set_placeholder_text(Some("Search music"));
    search_entry.set_hexpand(true);
    search_entry.set_width_chars(10);
    search_entry.add_css_class("sidebar-entry");
    search_entry.set_tooltip_text(Some("Search the public YouTube Music catalog"));
    search_row.append(&search_button);
    search_row.append(&search_entry);
    container.append(&search_row);

    let section = label("Session", "sidebar-section");
    section.set_margin_start(18);
    section.set_margin_top(11);
    section.set_margin_bottom(5);
    container.append(&section);

    let nav = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
    nav.set_margin_start(9);
    nav.set_margin_end(9);
    let home_button = nav_row("go-home-symbolic", "Home");
    let search_button_nav = nav_row("system-search-symbolic", "Search");
    let queue_button = nav_row("view-list-symbolic", "Queue");
    let queue_count = label("0", "nav-count");
    queue_count.set_valign(gtk4::Align::Center);
    if let Some(child) = queue_button.child() {
        if let Some(row) = child.downcast_ref::<gtk4::Box>() {
            row.append(&queue_count);
        }
    }
    let library_button = nav_row("folder-music-symbolic", "Library");
    nav.append(&library_button);
    nav.append(&home_button);
    nav.append(&search_button_nav);
    nav.append(&queue_button);
    container.append(&nav);

    let spacer = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    spacer.set_vexpand(true);
    container.append(&spacer);

    SidebarParts {
        settings_button,
        library_button,
        container,
        search_entry,
        search_button,
        home_button,
        search_button_nav,
        queue_button,
        queue_count,
    }
}

struct HomeParts {
    page: gtk4::Box,
    stack: gtk4::Stack,
    recent_flow: gtk4::FlowBox,
    recent_count: gtk4::Label,
    search_button: gtk4::Button,
}

fn build_home_page() -> HomeParts {
    let page = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    page.add_css_class("page");
    let header = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    header.add_css_class("page-header");
    header.set_margin_start(26);
    header.set_margin_end(26);
    header.set_margin_top(20);
    header.set_margin_bottom(12);
    header.append(&label("Home", "page-title"));
    page.append(&header);

    let stack = gtk4::Stack::new();
    stack.set_vexpand(true);
    stack.set_transition_type(gtk4::StackTransitionType::Crossfade);
    stack.set_transition_duration(140);

    let empty = gtk4::Box::new(gtk4::Orientation::Vertical, 6);
    empty.add_css_class("home-empty");
    empty.set_halign(gtk4::Align::Start);
    empty.set_valign(gtk4::Align::Start);
    empty.set_margin_start(26);
    empty.set_margin_end(26);
    empty.set_margin_top(4);
    empty.set_margin_bottom(26);
    let empty_title = label("Find something to play", "empty-title");
    empty.append(&empty_title);
    let empty_copy = label("Search for music to start this session.", "empty-copy");
    empty_copy.set_wrap(true);
    empty_copy.set_wrap_mode(pango::WrapMode::WordChar);
    empty_copy.set_max_width_chars(44);
    empty.append(&empty_copy);
    let search_button = gtk4::Button::with_label("Search music");
    search_button.add_css_class("accent");
    search_button.add_css_class("empty-action");
    search_button.set_halign(gtk4::Align::Start);
    search_button.set_margin_top(4);
    search_button.set_tooltip_text(Some("Focus the catalog search"));
    empty.append(&search_button);
    stack.add_named(&empty, Some("empty"));

    let scroll = gtk4::ScrolledWindow::builder()
        .hscrollbar_policy(gtk4::PolicyType::Never)
        .vscrollbar_policy(gtk4::PolicyType::Automatic)
        .vexpand(true)
        .build();
    scroll.add_css_class("page-scroll");
    let content = gtk4::Box::new(gtk4::Orientation::Vertical, 14);
    content.set_margin_start(26);
    content.set_margin_end(26);
    content.set_margin_bottom(26);
    let section = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
    section.append(&label("This session", "section-title"));
    let recent_count = label("", "page-meta");
    recent_count.set_hexpand(true);
    recent_count.set_halign(gtk4::Align::End);
    section.append(&recent_count);
    content.append(&section);
    let recent_flow = gtk4::FlowBox::new();
    recent_flow.add_css_class("recent-flow");
    recent_flow.set_selection_mode(gtk4::SelectionMode::None);
    recent_flow.set_homogeneous(true);
    recent_flow.set_min_children_per_line(2);
    recent_flow.set_max_children_per_line(4);
    recent_flow.set_column_spacing(HOME_CARD_GAP);
    recent_flow.set_row_spacing(22);
    recent_flow.set_valign(gtk4::Align::Start);
    content.append(&recent_flow);
    scroll.set_child(Some(&content));
    stack.add_named(&scroll, Some("recent"));
    stack.set_visible_child_name("empty");
    page.append(&stack);

    HomeParts {
        page,
        stack,
        recent_flow,
        recent_count,
        search_button,
    }
}

struct SearchParts {
    page: gtk4::Box,
    empty_label: gtk4::Label,
    results_stack: gtk4::Stack,
    results_list: gtk4::ListBox,
    results_hint: gtk4::Label,
}

fn build_search_page() -> SearchParts {
    let page = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    page.add_css_class("page");
    let header = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    header.add_css_class("page-header");
    header.set_margin_start(26);
    header.set_margin_end(26);
    header.set_margin_top(20);
    header.set_margin_bottom(12);
    header.append(&label("Search", "page-title"));
    page.append(&header);

    let result_header = gtk4::Box::new(gtk4::Orientation::Horizontal, 10);
    result_header.add_css_class("results-heading");
    result_header.set_margin_start(26);
    result_header.set_margin_end(26);
    result_header.set_margin_bottom(6);
    result_header.append(&label("Songs", "section-title"));
    let results_hint = label("Search the catalog from the sidebar", "page-meta");
    results_hint.set_hexpand(true);
    results_hint.set_halign(gtk4::Align::End);
    result_header.append(&results_hint);
    page.append(&result_header);

    let results_stack = gtk4::Stack::new();
    results_stack.set_vexpand(true);
    results_stack.set_transition_type(gtk4::StackTransitionType::Crossfade);
    results_stack.set_transition_duration(120);

    let empty = gtk4::Box::new(gtk4::Orientation::Vertical, 5);
    empty.add_css_class("search-empty");
    empty.set_halign(gtk4::Align::Start);
    empty.set_valign(gtk4::Align::Start);
    empty.set_margin_start(26);
    empty.set_margin_end(26);
    empty.set_margin_top(4);
    empty.set_margin_bottom(20);
    let empty_label = label("Find a song or artist to begin", "empty-title");
    empty_label.set_wrap(true);
    empty_label.set_wrap_mode(pango::WrapMode::WordChar);
    empty_label.set_max_width_chars(46);
    empty.append(&empty_label);
    let empty_copy = label("Search from the sidebar to see songs here.", "empty-copy");
    empty_copy.set_wrap(true);
    empty_copy.set_wrap_mode(pango::WrapMode::WordChar);
    empty_copy.set_max_width_chars(46);
    empty.append(&empty_copy);
    results_stack.add_named(&empty, Some("empty"));

    let results_list = gtk4::ListBox::new();
    results_list.add_css_class("results-list");
    results_list.set_selection_mode(gtk4::SelectionMode::Single);
    let results_scroll = gtk4::ScrolledWindow::builder()
        .hscrollbar_policy(gtk4::PolicyType::Never)
        .vscrollbar_policy(gtk4::PolicyType::Automatic)
        .vexpand(true)
        .child(&results_list)
        .build();
    results_scroll.add_css_class("results-scroll");
    results_stack.add_named(&results_scroll, Some("list"));
    results_stack.set_visible_child_name("empty");
    page.append(&results_stack);

    SearchParts {
        page,
        empty_label,
        results_stack,
        results_list,
        results_hint,
    }
}

struct PlayerParts {
    header: gtk4::HeaderBar,
    title: gtk4::Label,
    artist: gtk4::Label,
    play_icon: gtk4::Image,
    play_button: gtk4::Button,
    stop_button: gtk4::Button,
    previous_button: gtk4::Button,
    next_button: gtk4::Button,
    volume_scale: gtk4::Scale,
    queue_toggle: gtk4::Button,
    lyrics_toggle: gtk4::Button,
    artwork: ArtworkSlot,
    progress_scale: gtk4::Scale,
    progress_time: gtk4::Label,
}

fn build_player_bar() -> PlayerParts {
    let header = gtk4::HeaderBar::new();
    header.add_css_class("top-player");
    header.set_show_title_buttons(true);
    header.set_decoration_layout(Some(":close"));

    let transport = gtk4::Box::new(gtk4::Orientation::Horizontal, 1);
    transport.add_css_class("transport");
    transport.set_valign(gtk4::Align::Center);
    let previous_button = transport_icon_button("media-skip-backward-symbolic", "Previous");
    let next_button = transport_icon_button("media-skip-forward-symbolic", "Next");
    let play_icon = gtk4::Image::from_icon_name("media-playback-start-symbolic");
    play_icon.set_pixel_size(16);
    let play_button = gtk4::Button::new();
    play_button.set_child(Some(&play_icon));
    play_button.add_css_class("play-button");
    set_accessible_label(&play_button, "Play");
    let stop_button = transport_icon_button("media-playback-stop-symbolic", "Stop");
    transport.append(&previous_button);
    transport.append(&play_button);
    transport.append(&next_button);
    transport.append(&stop_button);
    header.pack_start(&transport);

    let (artwork_frame, artwork_picture) = artwork_view(36, 16);
    let now = gtk4::Box::new(gtk4::Orientation::Vertical, 2);
    now.add_css_class("now-playing");
    now.set_size_request(380, -1);
    now.set_hexpand(false);
    let line = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    line.set_valign(gtk4::Align::Center);
    let meta = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    meta.set_hexpand(true);
    meta.set_valign(gtk4::Align::Center);
    let title = label("Nothing playing", "player-title");
    title.set_ellipsize(pango::EllipsizeMode::End);
    title.set_max_width_chars(36);
    let artist = label("Search to start listening", "player-artist");
    artist.set_ellipsize(pango::EllipsizeMode::End);
    artist.set_max_width_chars(44);
    meta.append(&title);
    meta.append(&artist);
    let progress_time = label("0:00 / 0:00", "player-time");
    progress_time.set_valign(gtk4::Align::Center);
    line.append(&artwork_frame);
    line.append(&meta);
    line.append(&progress_time);
    now.append(&line);

    let progress_scale =
        gtk4::Scale::with_range(gtk4::Orientation::Horizontal, 0.0, PROGRESS_MAX, 1.0);
    progress_scale.set_draw_value(false);
    progress_scale.set_hexpand(true);
    progress_scale.set_size_request(-1, 8);
    progress_scale.add_css_class("progress-scale");
    progress_scale.set_tooltip_text(Some("Seek through the current recording"));
    now.append(&progress_scale);
    header.set_title_widget(Some(&now));

    let utility = gtk4::Box::new(gtk4::Orientation::Horizontal, 3);
    utility.set_valign(gtk4::Align::Center);
    utility.add_css_class("utility");
    let volume_icon = gtk4::Image::from_icon_name("audio-volume-high-symbolic");
    volume_icon.set_pixel_size(15);
    volume_icon.add_css_class("player-icon");
    let volume_scale = gtk4::Scale::with_range(gtk4::Orientation::Horizontal, 0.0, 100.0, 1.0);
    volume_scale.set_draw_value(false);
    volume_scale.set_value(DEFAULT_VOLUME * 100.0);
    volume_scale.set_size_request(64, -1);
    volume_scale.add_css_class("volume");
    volume_scale.set_tooltip_text(Some("Volume"));
    let lyrics_toggle = icon_button("text-x-generic-symbolic", "Lyrics");
    lyrics_toggle.add_css_class("top-action");
    let queue_toggle = icon_button("view-list-symbolic", "Queue");
    queue_toggle.add_css_class("top-action");
    utility.append(&volume_icon);
    utility.append(&volume_scale);
    let utility_gap = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    utility_gap.set_size_request(3, -1);
    utility.append(&utility_gap);
    utility.append(&lyrics_toggle);
    utility.append(&queue_toggle);
    header.pack_end(&utility);
    let artwork = ArtworkSlot::new(&artwork_picture);

    PlayerParts {
        header,
        title,
        artist,
        play_icon,
        play_button,
        stop_button,
        previous_button,
        next_button,
        volume_scale,
        queue_toggle,
        lyrics_toggle,
        artwork,
        progress_scale,
        progress_time,
    }
}

fn nav_row(icon: &str, text: &str) -> gtk4::Button {
    let button = gtk4::Button::new();
    button.add_css_class("nav-row");
    button.set_hexpand(true);
    let row = gtk4::Box::new(gtk4::Orientation::Horizontal, 9);
    let image = gtk4::Image::from_icon_name(icon);
    image.set_pixel_size(15);
    image.add_css_class("nav-icon");
    let name = label(text, "nav-label");
    name.set_hexpand(true);
    row.append(&image);
    row.append(&name);
    button.set_child(Some(&row));
    button
}

fn icon_button(icon: &str, tooltip: &str) -> gtk4::Button {
    let button = gtk4::Button::new();
    let image = gtk4::Image::from_icon_name(icon);
    image.set_pixel_size(15);
    button.set_child(Some(&image));
    button.add_css_class("icon-button");
    button.set_tooltip_text(Some(tooltip));
    set_accessible_label(&button, tooltip);
    button
}

fn set_accessible_label(widget: &impl IsA<gtk4::Accessible>, label: &str) {
    widget.update_property(&[gtk4::accessible::Property::Label(label)]);
}
fn install_css() {
    let provider = gtk4::CssProvider::new();
    provider.load_from_string(include_str!("style.css"));
    if let Some(display) = gdk::Display::default() {
        gtk4::style_context_add_provider_for_display(
            &display,
            &provider,
            gtk4::STYLE_PROVIDER_PRIORITY_APPLICATION,
        );
    }
}

struct SidePanel {
    container: gtk4::Box,
    close: gtk4::Button,
    title: gtk4::Label,
    stack: gtk4::Stack,
    queue_current_title: gtk4::Label,
    queue_current_artist: gtk4::Label,
    queue_current_duration: gtk4::Label,
    queue_list: gtk4::ListBox,
    queue_stack: gtk4::Stack,
    queue_count: gtk4::Label,
    queue_clear: gtk4::Button,
    lyrics_stack: gtk4::Stack,
    lyrics_spinner: gtk4::Spinner,
    lyrics_message: gtk4::Label,
    lyrics_retry: gtk4::Button,
    lyrics_scroll: gtk4::ScrolledWindow,
    lyrics_lines: gtk4::Box,
    lyrics_follow: gtk4::Button,
    lyrics_attribution: gtk4::Label,
}

fn build_side_panel() -> SidePanel {
    let container = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    container.add_css_class("side-panel");
    container.set_size_request(SIDE_PANEL_WIDTH, -1);
    container.set_visible(false);

    let side_header = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
    side_header.add_css_class("side-header");
    let title = label("Queue", "side-title");
    title.set_hexpand(true);
    let close = transport_icon_button("window-close-symbolic", "Close panel");
    close.add_css_class("panel-close");
    set_accessible_label(&close, "Close panel");
    side_header.append(&title);
    side_header.append(&close);
    container.append(&side_header);

    let stack = gtk4::Stack::new();
    stack.set_vexpand(true);
    stack.set_transition_type(gtk4::StackTransitionType::Crossfade);

    let queue_page = gtk4::Box::new(gtk4::Orientation::Vertical, 10);
    queue_page.add_css_class("side-page");
    queue_page.set_vexpand(true);

    queue_page.append(&label("Now playing", "side-section"));
    let queue_current = gtk4::Box::new(gtk4::Orientation::Vertical, 2);
    queue_current.add_css_class("queue-current");
    let queue_current_title = label("Nothing playing", "queue-current-title");
    queue_current_title.set_ellipsize(pango::EllipsizeMode::End);
    queue_current_title.set_max_width_chars(36);
    let queue_current_artist = label("Add a recording to begin.", "queue-current-artist");
    queue_current_artist.set_ellipsize(pango::EllipsizeMode::End);
    queue_current_artist.set_max_width_chars(38);
    let queue_current_duration = label("", "queue-current-duration");
    queue_current.append(&queue_current_title);
    queue_current.append(&queue_current_artist);
    queue_current.append(&queue_current_duration);
    queue_page.append(&queue_current);

    let upcoming_heading = gtk4::Box::new(gtk4::Orientation::Horizontal, 7);
    upcoming_heading.append(&label("Up next", "side-section"));
    let queue_count = label("0 up next", "muted");
    queue_count.set_hexpand(true);
    upcoming_heading.append(&queue_count);
    let queue_clear = gtk4::Button::with_label("Clear");
    queue_clear.add_css_class("text-button");
    queue_clear.set_tooltip_text(Some("Clear upcoming entries without stopping playback"));
    queue_clear.set_sensitive(false);
    upcoming_heading.append(&queue_clear);
    queue_page.append(&upcoming_heading);

    let queue_stack = gtk4::Stack::new();
    queue_stack.set_vexpand(true);
    let queue_list = gtk4::ListBox::new();
    queue_list.add_css_class("queue-list");
    queue_list.set_selection_mode(gtk4::SelectionMode::Single);
    queue_list.set_activate_on_single_click(true);
    let queue_scroll = gtk4::ScrolledWindow::builder()
        .hscrollbar_policy(gtk4::PolicyType::Never)
        .vscrollbar_policy(gtk4::PolicyType::Automatic)
        .vexpand(true)
        .child(&queue_list)
        .build();
    queue_scroll.add_css_class("queue-scroll");
    let queue_empty = label(
        "No upcoming recordings.\nAdd one from a search result.",
        "queue-empty",
    );
    queue_empty.set_justify(gtk4::Justification::Center);
    queue_empty.set_halign(gtk4::Align::Center);
    queue_empty.set_valign(gtk4::Align::Center);
    queue_empty.set_wrap(true);
    queue_empty.set_wrap_mode(pango::WrapMode::WordChar);
    queue_stack.add_named(&queue_scroll, Some("list"));
    queue_stack.add_named(&queue_empty, Some("empty"));
    queue_stack.set_visible_child_name("empty");
    queue_page.append(&queue_stack);
    stack.add_named(&queue_page, Some("queue"));

    let lyrics_page = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    lyrics_page.add_css_class("lyrics-page");
    lyrics_page.set_vexpand(true);
    let follow_row = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
    follow_row.add_css_class("lyrics-follow-row");
    let lyrics_follow = gtk4::Button::with_label("Resume following");
    lyrics_follow.add_css_class("lyrics-follow");
    lyrics_follow.set_tooltip_text(Some("Follow the current lyric line again"));
    lyrics_follow.set_visible(false);
    follow_row.append(&lyrics_follow);
    lyrics_page.append(&follow_row);

    let lyrics_stack = gtk4::Stack::new();
    lyrics_stack.set_vexpand(true);
    lyrics_stack.set_transition_type(gtk4::StackTransitionType::Crossfade);

    let loading_page = gtk4::Box::new(gtk4::Orientation::Vertical, 10);
    loading_page.set_halign(gtk4::Align::Center);
    loading_page.set_valign(gtk4::Align::Center);
    loading_page.set_margin_start(20);
    loading_page.set_margin_end(20);
    let lyrics_spinner = gtk4::Spinner::new();
    let loading_label = label("Looking up lyrics…", "lyrics-message");
    loading_label.set_justify(gtk4::Justification::Center);
    loading_label.set_halign(gtk4::Align::Center);
    loading_page.append(&lyrics_spinner);
    loading_page.append(&loading_label);
    lyrics_stack.add_named(&loading_page, Some("loading"));

    let message_page = gtk4::Box::new(gtk4::Orientation::Vertical, 12);
    message_page.set_halign(gtk4::Align::Center);
    message_page.set_valign(gtk4::Align::Center);
    message_page.set_margin_start(22);
    message_page.set_margin_end(22);
    let lyrics_message = label("Select a recording to see its lyrics.", "lyrics-message");
    lyrics_message.set_justify(gtk4::Justification::Center);
    lyrics_message.set_halign(gtk4::Align::Center);
    lyrics_message.set_wrap(true);
    lyrics_message.set_wrap_mode(pango::WrapMode::WordChar);
    lyrics_message.set_max_width_chars(36);
    let lyrics_retry = gtk4::Button::with_label("Retry lyrics");
    lyrics_retry.add_css_class("accent");
    lyrics_retry.set_visible(false);
    message_page.append(&lyrics_message);
    message_page.append(&lyrics_retry);
    lyrics_stack.add_named(&message_page, Some("message"));

    let lyrics_scroll = gtk4::ScrolledWindow::builder()
        .hscrollbar_policy(gtk4::PolicyType::Never)
        .vscrollbar_policy(gtk4::PolicyType::Automatic)
        .vexpand(true)
        .build();
    lyrics_scroll.add_css_class("lyrics-scroll");
    let lyrics_lines = gtk4::Box::new(gtk4::Orientation::Vertical, 2);
    lyrics_lines.add_css_class("lyrics-lines");
    lyrics_lines.set_margin_top(4);
    lyrics_lines.set_margin_bottom(22);
    lyrics_lines.set_margin_start(13);
    lyrics_lines.set_margin_end(13);
    lyrics_scroll.set_child(Some(&lyrics_lines));
    lyrics_stack.add_named(&lyrics_scroll, Some("lines"));
    lyrics_stack.set_visible_child_name("message");
    lyrics_page.append(&lyrics_stack);

    let lyrics_attribution = label("", "lyrics-attribution");
    lyrics_attribution.set_visible(false);
    lyrics_attribution.set_margin_start(16);
    lyrics_attribution.set_margin_end(16);
    lyrics_attribution.set_margin_bottom(10);
    lyrics_page.append(&lyrics_attribution);
    stack.add_named(&lyrics_page, Some("lyrics"));
    stack.set_visible_child_name("queue");
    container.append(&stack);

    SidePanel {
        container,
        close,
        title,
        stack,
        queue_current_title,
        queue_current_artist,
        queue_current_duration,
        queue_list,
        queue_stack,
        queue_count,
        queue_clear,
        lyrics_stack,
        lyrics_spinner,
        lyrics_message,
        lyrics_retry,
        lyrics_scroll,
        lyrics_lines,
        lyrics_follow,
        lyrics_attribution,
    }
}

struct AppUi {
    library: Rc<RefCell<crate::library::LibraryPane>>,
    library_seq: u64,
    nav_library: gtk4::Button,
    window: gtk4::ApplicationWindow,
    search_entry: gtk4::Entry,
    search_button: gtk4::Button,
    spinner: gtk4::Spinner,
    status_label: gtk4::Label,
    main_stack: gtk4::Stack,
    nav_home: gtk4::Button,
    nav_search: gtk4::Button,
    nav_queue: gtk4::Button,
    queue_nav_count: gtk4::Label,
    home_stack: gtk4::Stack,
    home_search_button: gtk4::Button,
    recent_flow: gtk4::FlowBox,
    recent_count: gtk4::Label,
    empty_label: gtk4::Label,
    results_hint: gtk4::Label,
    results_stack: gtk4::Stack,
    results_list: gtk4::ListBox,
    title_label: gtk4::Label,
    artist_label: gtk4::Label,
    play_icon: gtk4::Image,
    play_button: gtk4::Button,
    stop_button: gtk4::Button,
    previous_button: gtk4::Button,
    next_button: gtk4::Button,
    volume_scale: gtk4::Scale,
    volume: f64,
    progress_scale: gtk4::Scale,
    progress_time: gtk4::Label,
    progress_suppress: Rc<Cell<bool>>,
    player_art: ArtworkSlot,
    artwork_loader: Rc<RefCell<ArtworkLoader>>,
    tracks: Vec<Track>,
    recent_tracks: Vec<Track>,
    bridge: Option<BridgeService>,
    player_tx: mpsc::Sender<PlayerEvent>,
    player: Option<Player>,
    player_ready: bool,
    queue: Queue,
    playback: PlaybackState,
    prepared: Option<ResolveData>,
    cached_playback: Option<String>,
    search_seq: u64,
    resolve_seq: u64,
    side_panel: gtk4::Box,
    side_close: gtk4::Button,
    side_title: gtk4::Label,
    side_stack: gtk4::Stack,
    queue_toggle: gtk4::Button,
    lyrics_toggle: gtk4::Button,
    queue_current_title: gtk4::Label,
    queue_current_artist: gtk4::Label,
    queue_current_duration: gtk4::Label,
    queue_list: gtk4::ListBox,
    queue_stack: gtk4::Stack,
    queue_count: gtk4::Label,
    queue_clear: gtk4::Button,
    lyrics_stack: gtk4::Stack,
    lyrics_spinner: gtk4::Spinner,
    lyrics_message: gtk4::Label,
    lyrics_retry: gtk4::Button,
    lyrics_scroll: gtk4::ScrolledWindow,
    lyrics_lines: gtk4::Box,
    lyrics_follow: gtk4::Button,
    lyrics_attribution: gtk4::Label,
    lyrics_data: Option<LyricsData>,
    lyrics_loading: bool,
    lyrics_seq: u64,
    lyrics_track_id: Option<String>,
    lyrics_following: bool,
    lyrics_active: Option<usize>,
    lyrics_line_labels: Vec<gtk4::Label>,
    lyrics_line_widgets: Vec<gtk4::Widget>,
    lyrics_suppress_scroll: Rc<Cell<bool>>,
    lyrics_scroll_animation: Option<glib::SourceId>,
    self_weak: Weak<RefCell<AppUi>>,
    closing: bool,
}

impl AppUi {
    fn start_search(&mut self) {
        if self.closing {
            return;
        }
        let query = match normalize_query(&self.search_entry.text()) {
            Ok(query) => query,
            Err(error) => {
                self.navigate("search");
                self.set_status(error.message(), true);
                return;
            }
        };
        let result = match self.bridge.as_ref() {
            Some(bridge) => bridge.submit_search(&query),
            None => {
                self.set_status(
                    "The Python music bridge is not configured. Set EMBER_CHECKOUT_ROOT and restart.",
                    true,
                );
                return;
            }
        };
        match result {
            Ok(seq) => {
                self.search_seq = seq;
                self.navigate("search");
                self.clear_results("Searching YouTube Music…");
                self.set_loading_status("Searching YouTube Music…");
            }
            Err(error) => self.set_status(error.message(), true),
        }
    }

    fn handle_job(&mut self, job: JobResult) {
        if self.closing {
            return;
        }
        match job.kind {
            JobKind::Library if job.seq == self.library_seq => {
                let result = match job.result {
                    Ok(BridgePayload::Library(state)) => {
                        if !state.error.is_empty() {
                            self.set_status(&state.error, false);
                        } else {
                            self.set_status("Shared library updated.", false);
                        }
                        Ok(state)
                    }
                    _ => Err("Shared library request failed.".to_owned()),
                };
                crate::library::LibraryPane::apply(&self.library, result);
            }
            JobKind::Search if job.seq == self.search_seq => match job.result {
                Ok(BridgePayload::Search(tracks)) => self.show_search_results(tracks),
                Ok(_) => self.set_status("The music bridge returned the wrong result type.", true),
                Err(error) => {
                    self.clear_results("Search failed.");
                    self.set_status(&format!("Search failed. {}", error.message()), true);
                }
            },
            JobKind::Resolve if job.seq == self.resolve_seq => match job.result {
                Ok(BridgePayload::Resolve(data)) => self.handle_resolved(data),
                Ok(_) => self.fail_playback("The music bridge returned the wrong result type."),
                Err(error) => self.fail_playback(error.message()),
            },
            JobKind::Lyrics if job.seq == self.lyrics_seq => match job.result {
                Ok(BridgePayload::Lyrics(data)) => self.handle_lyrics(data),
                Ok(_) => self.lyrics_failed("The lyrics bridge returned the wrong result type."),
                Err(error) => self.lyrics_failed(error.message()),
            },
            JobKind::Cache => {
                match job.result {
                    Ok(BridgePayload::Cache { cleared: true }) => self.set_status("Audio cache cleared.", false),
                    Err(error) if error.code() != "cancelled" => self.set_status("Audio cache unavailable; streaming still works. Check storage or try again later.", false),
                    _ => {}
                }
            }
            _ => {}
        }
    }

    fn show_search_results(&mut self, tracks: Vec<Track>) {
        self.remember_tracks(&tracks);
        self.tracks = tracks;
        while let Some(child) = self.results_list.first_child() {
            self.results_list.remove(&child);
        }
        if self.tracks.is_empty() {
            self.results_stack.set_visible_child_name("empty");
            self.empty_label
                .set_text("No matching songs. Try another search.");
            self.results_hint.set_text("No results");
            self.set_status("No songs found.", false);
            return;
        }
        let weak = self.self_weak.clone();
        for track in &self.tracks {
            let (row, slot) = track_row(track, &weak);
            self.results_list.append(&row);
            if let Some(url) = track.artwork_url.as_deref() {
                ArtworkLoader::request(&self.artwork_loader, url, slot);
            }
        }
        self.results_list.unselect_all();
        self.results_stack.set_visible_child_name("list");
        let count = self.tracks.len();
        self.results_hint
            .set_text(&format!("{count} {}", noun(count, "song", "songs")));
        self.set_status(
            &format!(
                "{count} {} found. Select one to play.",
                noun(count, "result", "results")
            ),
            false,
        );
    }

    fn clear_results(&mut self, message: &str) {
        self.tracks.clear();
        while let Some(child) = self.results_list.first_child() {
            self.results_list.remove(&child);
        }
        self.empty_label.set_text(message);
        self.results_stack.set_visible_child_name("empty");
        self.results_hint.set_text(message);
    }

    fn activate_search_result(&mut self, index: usize) {
        if self.closing {
            return;
        }
        let tracks = self.tracks.clone();
        let Some(entry) = self.queue.replace_search(&tracks, index) else {
            self.set_status("No recording was selected.", true);
            return;
        };
        self.refresh_queue_view();
        self.remember_selected(&entry.track);
        self.begin_entry(&entry, true);
    }

    fn activate_recent(&mut self, index: usize) {
        if self.closing {
            return;
        }
        let tracks = self.recent_tracks.clone();
        let Some(entry) = self.queue.replace_search(&tracks, index) else {
            return;
        };
        self.refresh_queue_view();
        self.remember_selected(&entry.track);
        self.begin_entry(&entry, true);
    }

    fn remember_tracks(&mut self, tracks: &[Track]) {
        let mut fresh = Vec::new();
        for track in tracks {
            if !fresh.iter().any(|item: &Track| item.id == track.id) {
                fresh.push(track.clone());
            }
        }
        for track in &self.recent_tracks {
            if !fresh.iter().any(|item: &Track| item.id == track.id) {
                fresh.push(track.clone());
            }
        }
        fresh.truncate(10);
        self.recent_tracks = fresh;
        self.refresh_home();
    }

    fn remember_selected(&mut self, track: &Track) {
        self.recent_tracks.retain(|item| item.id != track.id);
        self.recent_tracks.insert(0, track.clone());
        self.recent_tracks.truncate(10);
        self.refresh_home();
    }

    fn refresh_home(&mut self) {
        while let Some(child) = self.recent_flow.first_child() {
            self.recent_flow.remove(&child);
        }
        if self.recent_tracks.is_empty() {
            self.home_stack.set_visible_child_name("empty");
            self.recent_count.set_text("");
            return;
        }
        self.home_stack.set_visible_child_name("recent");
        let count = self.recent_tracks.len();
        self.recent_count.set_text(&format!(
            "{count} {}",
            noun(count, "recording", "recordings")
        ));
        let weak = self.self_weak.clone();
        let tracks = self.recent_tracks.clone();
        for (index, track) in tracks.iter().enumerate() {
            let (card, slot) = recent_card(track, index, &weak);
            self.recent_flow.insert(&card, -1);
            if let Some(url) = track.artwork_url.as_deref() {
                ArtworkLoader::request(&self.artwork_loader, url, slot);
            }
        }
    }

    fn set_player_artwork(&mut self, track: Option<&Track>) {
        self.player_art.clear();
        if let Some(url) = track.and_then(|track| track.artwork_url.as_deref()) {
            ArtworkLoader::request(&self.artwork_loader, url, self.player_art.clone());
        }
    }

    fn play_queue_entry(&mut self, id: EntryId) {
        if self.closing {
            return;
        }
        let Some(entry) = self.queue.select(id) else {
            return;
        };
        self.refresh_queue_view();
        self.begin_entry(&entry, true);
    }

    fn begin_current(&mut self, refresh_lyrics: bool) {
        let Some(entry) = self.queue.current().cloned() else {
            return;
        };
        self.begin_entry(&entry, refresh_lyrics);
    }

    fn begin_entry(&mut self, entry: &QueueEntry, refresh_lyrics: bool) {
        self.cached_playback = None;
        if let Some(bridge) = &self.bridge {
            bridge.cancel_cache();
        }
        if self.closing {
            return;
        }
        self.player = None;
        self.prepared = None;
        self.update_now_playing_labels();
        self.set_player_artwork(Some(&entry.track));
        self.refresh_queue_view();
        if refresh_lyrics {
            self.request_lyrics(&entry.track);
        }
        if !self.player_ready {
            self.playback.fail();
            self.set_play_icon(false);
            self.refresh_transport();
            self.set_status(
                "GStreamer playback is unavailable. Fix the plugin setup shown above.",
                true,
            );
            return;
        }
        let Some(bridge) = self.bridge.as_ref() else {
            self.playback.fail();
            self.set_play_icon(false);
            self.refresh_transport();
            self.set_status("The Python music bridge is not configured.", true);
            return;
        };
        let seq = match bridge.submit_resolve(&entry.track) {
            Ok(seq) => seq,
            Err(error) => {
                self.playback.fail();
                self.set_play_icon(false);
                self.refresh_transport();
                self.set_status(error.message(), true);
                return;
            }
        };
        self.resolve_seq = seq;
        self.playback.begin(entry.id, entry.track.id.clone(), seq);
        self.set_play_icon(false);
        self.refresh_transport();
        self.set_loading_status("Loading recording…");
    }

    fn handle_resolved(&mut self, data: ResolveData) {
        if self.closing {
            return;
        }
        let disposition = self
            .playback
            .accept_resolve(self.resolve_seq, &data.track.id);
        if disposition == ResolveDisposition::Stale {
            return;
        }
        if self.queue.current().map(|entry| entry.track.id.as_str()) != Some(data.track.id.as_str())
        {
            self.fail_playback("The resolved recording did not match the selection.");
            return;
        }
        self.title_label.set_text(&data.track.name);
        self.artist_label.set_text(&data.track.artist_line());
        self.prepared = Some(data);
        match disposition {
            ResolveDisposition::Start => self.start_prepared_playback(),
            ResolveDisposition::Ready => {
                self.set_play_icon(false);
                self.refresh_transport();
                self.set_status("Audio is ready. Press Play to start.", false);
            }
            ResolveDisposition::Stale => {}
        }
    }

    fn start_prepared_playback(&mut self) {
        if self.closing || !self.playback.wants_play() {
            return;
        }
        let Some(data) = self.prepared.take() else {
            return;
        };
        self.player = None;
        let player = match Player::new(self.player_tx.clone()) {
            Ok(player) => player,
            Err(message) => {
                self.fail_playback(&message);
                return;
            }
        };
        self.cached_playback = data
            .stream_url
            .starts_with("file:")
            .then(|| data.stream_url.clone());
        player.set_volume(self.volume / 100.0);
        if let Err(message) = player.play_uri(&data.stream_url) {
            self.fail_playback(&message);
            return;
        }
        if !data.stream_url.starts_with("file:") {
            if let Some(bridge) = &self.bridge {
                bridge.cache_track(&data.track);
            }
        }
        self.player = Some(player);
        self.playback.mark_starting();
        self.set_loading_status("Starting playback…");
        self.set_play_icon(true);
        self.refresh_transport();
    }

    fn fail_playback(&mut self, message: &str) {
        if self.closing {
            return;
        }
        if let Some(uri) = self.cached_playback.take() {
            self.player = None;
            if crate::cache::valid_local_uri(&uri) {
                if let Some(path) = gtk4::gio::File::for_uri(&uri).path() {
                    if std::fs::remove_file(path).is_ok() {
                        self.begin_current(false);
                        return;
                    }
                }
            } else {
                self.begin_current(false);
                return;
            }
        }
        self.player = None;
        self.prepared = None;
        self.playback.fail();
        self.resolve_seq = self.resolve_seq.saturating_add(1);
        self.update_now_playing_labels();
        self.refresh_progress();
        self.set_play_icon(false);
        self.refresh_transport();
        self.set_status(
            &format!("{message} Press Play to retry, Next to skip, or open Queue."),
            true,
        );
    }

    fn toggle_play(&mut self) {
        if self.closing {
            return;
        }
        if self.queue.current().is_none() {
            if self.queue.next_index().is_some() {
                if let Some(entry) = self.queue.select_index(0) {
                    self.refresh_queue_view();
                    self.begin_entry(&entry, true);
                }
            } else {
                self.set_status("Search for a recording to begin.", false);
            }
            return;
        }
        if !self.player_ready {
            self.set_status("GStreamer playback is unavailable.", true);
            return;
        }
        if self.playback.wants_play() {
            match self.playback.pause() {
                PauseAction::CancelPending => {
                    self.set_play_icon(false);
                    self.refresh_transport();
                    self.set_status(
                        "Preparation continues. Playback will stay silent until you press Play.",
                        false,
                    );
                    return;
                }
                PauseAction::PauseGst => {
                    if let Some(player) = self.player.as_ref() {
                        if let Err(message) = player.pause() {
                            self.fail_playback(&message);
                            return;
                        }
                    }
                    self.set_play_icon(false);
                    self.refresh_transport();
                    self.set_status("Paused.", false);
                    return;
                }
                PauseAction::None => {}
            }
        }
        match self.playback.play() {
            PlayAction::StartPrepared => self.start_prepared_playback(),
            PlayAction::Resume => {
                let Some(player) = self.player.as_ref() else {
                    self.begin_current(false);
                    return;
                };
                match player.play() {
                    Ok(()) => {
                        self.set_play_icon(true);
                        self.set_loading_status("Resuming…");
                        self.refresh_transport();
                    }
                    Err(message) => self.fail_playback(&message),
                }
            }
            PlayAction::Resolve => self.begin_current(false),
            PlayAction::None => {
                if self.playback.is_preparing() {
                    self.set_play_icon(true);
                    self.refresh_transport();
                    self.set_status("Playback will start when the audio is ready.", false);
                } else {
                    self.refresh_transport();
                }
            }
        }
    }

    fn stop_playback(&mut self) {
        if self.closing {
            return;
        }
        self.gate_stop();
        self.update_now_playing_labels();
        self.set_play_icon(false);
        self.refresh_transport();
        if let Some(entry) = self.queue.current() {
            self.set_status(
                &format!(
                    "Stopped. Queue preserved; Play will restart “{}”.",
                    entry.track.name
                ),
                false,
            );
        } else {
            self.set_status("Stopped.", false);
        }
    }

    fn gate_stop(&mut self) {
        if let Some(player) = self.player.as_ref() {
            let _ = player.stop();
        }
        self.player = None;
        self.prepared = None;
        self.resolve_seq = self.resolve_seq.saturating_add(1);
        if let Some(bridge) = self.bridge.as_ref() {
            bridge.cancel_resolve();
        }
        self.playback.stop();
    }

    fn next_track(&mut self) {
        if self.closing {
            return;
        }
        if self.queue.current().is_none() {
            if let Some(entry) = self.queue.select_index(0) {
                self.refresh_queue_view();
                self.begin_entry(&entry, true);
            } else {
                self.set_status("The queue is empty.", false);
            }
            return;
        }
        let Some(index) = self.queue.next_index() else {
            self.set_status("End of queue.", false);
            return;
        };
        if let Some(entry) = self.queue.select_index(index) {
            self.refresh_queue_view();
            self.begin_entry(&entry, true);
        }
    }

    fn previous_track(&mut self) {
        if self.closing {
            return;
        }
        if self.queue.current().is_none() {
            if let Some(entry) = self.queue.select_index(0) {
                self.refresh_queue_view();
                self.begin_entry(&entry, true);
            }
            return;
        }
        let position = self
            .player
            .as_ref()
            .and_then(Player::position_ms)
            .unwrap_or(0);
        if self.playback.stream_loaded() && position >= 3_000 {
            self.restart_current_audio();
            return;
        }
        if let Some(index) = self.queue.previous_index() {
            if let Some(entry) = self.queue.select_index(index) {
                self.refresh_queue_view();
                self.begin_entry(&entry, true);
                return;
            }
        }
        self.restart_current_audio();
    }

    fn restart_current_audio(&mut self) {
        if self.playback.stream_loaded() {
            let Some(player) = self.player.as_ref() else {
                self.begin_current(false);
                return;
            };
            if let Err(message) = player.seek_to_ms(0) {
                self.set_status(&message, true);
                return;
            }
            self.set_status("Restarted the current recording.", false);
            self.tick_lyrics();
            return;
        }
        self.begin_current(false);
    }

    fn queue_play_next(&mut self, track: Track) {
        let entry = self.queue.play_next(&track);
        self.refresh_queue_view();
        if self.queue.current_id().is_none() {
            self.set_status(
                &format!(
                    "Queued “{}”. Press Play to start the pending queue.",
                    entry.track.name
                ),
                false,
            );
        } else {
            self.set_status(&format!("“{}” will play next.", entry.track.name), false);
        }
        self.refresh_transport();
    }

    fn queue_add(&mut self, track: Track) {
        let entry = self.queue.add(&track);
        self.refresh_queue_view();
        if self.queue.current_id().is_none() {
            self.set_status(
                &format!(
                    "Queued “{}”. Press Play to start the pending queue.",
                    entry.track.name
                ),
                false,
            );
        } else {
            self.set_status(
                &format!("Added “{}” to the queue.", entry.track.name),
                false,
            );
        }
        self.refresh_transport();
    }

    fn queue_remove(&mut self, id: EntryId) {
        if self.queue.remove_upcoming(id) {
            self.refresh_queue_view();
            self.set_status(
                "Removed the upcoming recording. Playback was not interrupted.",
                false,
            );
            self.refresh_transport();
        }
    }

    fn queue_move_up(&mut self, id: EntryId) {
        self.queue_move(id, -1);
    }

    fn queue_move_down(&mut self, id: EntryId) {
        self.queue_move(id, 1);
    }

    fn queue_move(&mut self, id: EntryId, offset: isize) {
        if self.queue.move_upcoming(id, offset) {
            self.refresh_queue_view();
            self.set_status("Queue order updated. Playback was not interrupted.", false);
        }
    }

    fn queue_clear(&mut self) {
        if self.queue.clear_upcoming() {
            self.refresh_queue_view();
            self.set_status(
                "Cleared upcoming recordings. Playback was not interrupted.",
                false,
            );
            self.refresh_transport();
        }
    }

    fn refresh_queue_view(&mut self) {
        let current = self.queue.current().cloned();
        if let Some(entry) = current.as_ref() {
            self.queue_current_title.set_text(&entry.track.name);
            self.queue_current_artist
                .set_text(&entry.track.artist_line());
            self.queue_current_duration
                .set_text(&entry.track.duration_text());
        } else {
            self.queue_current_title.set_text("Nothing playing");
            if self.queue.is_empty() {
                self.queue_current_artist
                    .set_text("Add a recording to begin.");
            } else {
                self.queue_current_artist
                    .set_text("Pending queue. Press Play to start.");
            }
            self.queue_current_duration.set_text("");
        }

        while let Some(child) = self.queue_list.first_child() {
            self.queue_list.remove(&child);
        }
        let upcoming: Vec<QueueEntry> = self.queue.upcoming().cloned().collect();
        if upcoming.is_empty() {
            self.queue_stack.set_visible_child_name("empty");
        } else {
            let weak = self.self_weak.clone();
            for (index, entry) in upcoming.iter().enumerate() {
                let can_up = index > 0;
                let can_down = index + 1 < upcoming.len();
                self.queue_list
                    .append(&queue_row(entry, index + 1, can_up, can_down, &weak));
            }
            self.queue_list.unselect_all();
            self.queue_stack.set_visible_child_name("list");
        }
        let count = upcoming.len();
        self.queue_count.set_text(&format!("{count} up next"));
        self.queue_nav_count.set_text(&count.to_string());
        self.queue_nav_count.set_visible(count > 0);
        self.queue_clear.set_sensitive(count > 0);
        self.update_now_playing_labels();
    }

    fn update_now_playing_labels(&self) {
        if let Some(entry) = self.queue.current() {
            self.title_label.set_text(&entry.track.name);
            self.artist_label.set_text(&entry.track.artist_line());
        } else if self.queue.is_empty() {
            self.title_label.set_text("Nothing playing");
            self.artist_label.set_text("Search to start listening");
        } else {
            self.title_label.set_text("Pending queue");
            self.artist_label.set_text("Press Play to start the queue");
        }
    }

    fn handle_player_event(&mut self, event: PlayerEvent) {
        if self.closing {
            return;
        }
        let Some(player_id) = self.player.as_ref().map(Player::id) else {
            return;
        };
        if player_id != event.player_id {
            return;
        }
        match event.kind {
            PlayerEventKind::Playing => {
                if self.playback.on_playing() {
                    self.set_play_icon(true);
                    self.refresh_transport();
                    self.set_status("Playing.", false);
                    self.tick_lyrics();
                } else {
                    if let Some(player) = self.player.as_ref() {
                        let _ = player.pause();
                    }
                    self.set_play_icon(false);
                    self.refresh_transport();
                    self.set_status("Paused.", false);
                }
            }
            PlayerEventKind::Eos => self.handle_eos(),
            PlayerEventKind::Error => {
                if self.playback.stream_loaded() || self.playback.is_preparing() {
                    self.fail_playback(
                        "Playback failed. Check the GStreamer base/good/soup plugins and try another recording.",
                    );
                }
            }
        }
    }

    fn handle_eos(&mut self) {
        if !self.playback.eos_begin() {
            return;
        }
        if let Some(index) = self.queue.next_index() {
            if self.queue.select_index(index).is_some() {
                self.refresh_queue_view();
                self.begin_current(true);
                return;
            }
        }
        self.player = None;
        self.prepared = None;
        self.playback.finish();
        self.update_now_playing_labels();
        self.set_play_icon(false);
        self.refresh_transport();
        self.set_status(
            "Queue finished. Press Play to restart the selected recording.",
            false,
        );
    }

    fn set_volume(&mut self, volume: f64) {
        self.volume = volume.clamp(0.0, 100.0);
        if let Some(player) = self.player.as_ref() {
            player.set_volume(self.volume / 100.0);
        }
    }

    fn refresh_progress(&self) {
        let fallback_duration = self
            .queue
            .current()
            .map(|entry| entry.track.duration_ms)
            .unwrap_or(0);
        let duration = self
            .player
            .as_ref()
            .and_then(Player::duration_ms)
            .filter(|duration| *duration > 0)
            .unwrap_or(fallback_duration);
        let elapsed = self
            .player
            .as_ref()
            .and_then(Player::position_ms)
            .unwrap_or(0)
            .min(duration);
        self.progress_time.set_text(&format!(
            "{} / {}",
            format_time(elapsed),
            format_time(duration)
        ));
        let fraction = if duration > 0 {
            elapsed as f64 / duration as f64
        } else {
            0.0
        };
        self.progress_suppress.set(true);
        self.progress_scale.set_value(fraction * PROGRESS_MAX);
        self.progress_suppress.set(false);
        self.progress_scale.set_sensitive(
            !self.closing && self.player.is_some() && self.playback.stream_loaded() && duration > 0,
        );
    }

    fn tick_progress(&self) {
        self.refresh_progress();
    }

    fn seek_to_progress(&mut self, fraction: f64) {
        let Some(player) = self.player.as_ref() else {
            self.set_status("Start playback before seeking.", false);
            self.refresh_progress();
            return;
        };
        if !self.playback.stream_loaded() {
            self.set_status("Start playback before seeking.", false);
            self.refresh_progress();
            return;
        }
        let fallback = self
            .queue
            .current()
            .map(|entry| entry.track.duration_ms)
            .unwrap_or(0);
        let duration = player
            .duration_ms()
            .filter(|duration| *duration > 0)
            .unwrap_or(fallback);
        let Some(target) = seek_target(fraction, duration) else {
            return;
        };
        match player.seek_to_ms(target) {
            Ok(()) => self.tick_lyrics(),
            Err(message) => self.set_status(&message, true),
        }
        self.refresh_progress();
    }

    fn navigate(&mut self, page: &str) {
        if self.side_panel.is_visible() {
            self.close_side_panel();
        }
        self.main_stack.set_visible_child_name(page);
        self.sync_nav();
        if page == "search" {
            self.search_entry.grab_focus();
        }
        if page == "library" {
            crate::library::LibraryPane::request(
                &self.library,
                serde_json::json!({"operation":"refresh"}),
            );
        }
    }

    fn sync_nav(&self) {
        let page = self
            .main_stack
            .visible_child_name()
            .map(|name| name.to_string())
            .unwrap_or_else(|| "home".to_owned());
        let queue_open = self.side_panel.is_visible()
            && self.side_stack.visible_child_name().as_deref() == Some("queue");
        let lyrics_open = self.side_panel.is_visible()
            && self.side_stack.visible_child_name().as_deref() == Some("lyrics");
        set_active(&self.nav_library, page == "library" && !queue_open);
        set_active(&self.nav_home, page == "home" && !queue_open);
        set_active(&self.nav_search, page == "search" && !queue_open);
        set_active(&self.nav_queue, queue_open);
        set_active(&self.queue_toggle, queue_open);
        set_active(&self.lyrics_toggle, lyrics_open);
    }

    fn set_play_icon(&self, playing: bool) {
        self.play_icon.set_icon_name(Some(if playing {
            "media-playback-pause-symbolic"
        } else {
            "media-playback-start-symbolic"
        }));
        self.play_button
            .set_tooltip_text(Some(if playing { "Pause" } else { "Play" }));
        set_accessible_label(&self.play_button, if playing { "Pause" } else { "Play" });
    }

    fn refresh_transport(&self) {
        let has_selection = self.queue.current().is_some() || !self.queue.is_empty();
        self.play_button.set_sensitive(
            !self.closing && self.bridge.is_some() && self.player_ready && has_selection,
        );
        self.stop_button.set_sensitive(self.playback.can_stop());
        self.previous_button
            .set_sensitive(has_selection && !self.closing);
        self.next_button
            .set_sensitive(self.queue.next_index().is_some() && !self.closing);

        let show_pause = self.playback.wants_play()
            && (self.playback.is_playing()
                || self.playback.is_preparing()
                || self.playback.stream_loaded());
        self.set_play_icon(show_pause);

        if self.playback.loading() && !self.closing {
            self.spinner.set_visible(true);
            self.spinner.start();
        } else {
            self.spinner.stop();
            self.spinner.set_visible(false);
        }
        self.queue_clear
            .set_sensitive(self.queue.upcoming_count() > 0);
        let count = self.queue.upcoming_count();
        self.queue_nav_count.set_text(&count.to_string());
        self.queue_nav_count.set_visible(count > 0);
        self.update_now_playing_labels();
        self.refresh_progress();
    }

    fn set_status(&mut self, text: &str, error: bool) {
        self.spinner.stop();
        self.spinner.set_visible(false);
        self.status_label.set_text(text);
        self.status_label.set_tooltip_text(Some(text));
        if error {
            self.status_label.add_css_class("error");
        } else {
            self.status_label.remove_css_class("error");
        }
    }

    fn set_loading_status(&mut self, text: &str) {
        self.status_label.remove_css_class("error");
        self.status_label.set_text(text);
        self.spinner.set_visible(true);
        self.spinner.start();
    }

    fn show_queue_panel(&mut self) {
        self.cancel_lyrics_scroll();
        self.refresh_queue_view();
        self.side_title.set_text("Queue");
        self.side_stack.set_visible_child_name("queue");
        self.side_panel.set_visible(true);
        self.sync_nav();
    }

    fn show_lyrics_panel(&mut self) {
        self.side_title.set_text("Lyrics");
        self.side_stack.set_visible_child_name("lyrics");
        self.side_panel.set_visible(true);
        self.sync_nav();
        self.suppress_lyrics_scroll_for(Duration::from_millis(180));
        self.update_lyrics_highlight_from_player();
        if self.lyrics_following {
            self.schedule_lyrics_scroll();
        }
    }

    fn close_side_panel(&mut self) {
        self.cancel_lyrics_scroll();
        self.side_panel.set_visible(false);
        self.sync_nav();
    }

    fn toggle_panel(&mut self, name: &str) {
        let same = self.side_panel.is_visible()
            && self.side_stack.visible_child_name().as_deref() == Some(name);
        if same {
            self.close_side_panel();
        } else if name == "queue" {
            self.show_queue_panel();
        } else {
            self.show_lyrics_panel();
        }
    }

    fn request_lyrics(&mut self, track: &Track) {
        self.cancel_lyrics_scroll();
        if self.closing {
            return;
        }
        self.lyrics_track_id = Some(track.id.clone());
        self.lyrics_seq = 0;
        self.lyrics_data = None;
        self.lyrics_loading = true;
        self.lyrics_following = true;
        self.lyrics_active = None;
        self.lyrics_follow.set_visible(false);
        self.lyrics_attribution.set_visible(false);
        self.clear_lyric_rows();
        self.show_lyrics_loading();
        let Some(bridge) = self.bridge.as_ref() else {
            self.lyrics_failed("Lyrics need the checkout Python bridge.");
            return;
        };
        bridge.cancel_lyrics();
        match bridge.submit_lyrics(track) {
            Ok(seq) => self.lyrics_seq = seq,
            Err(error) => self.lyrics_failed(error.message()),
        }
    }

    fn handle_lyrics(&mut self, data: LyricsData) {
        if self.closing {
            return;
        }
        if self.lyrics_track_id.as_deref() != Some(data.track_id.as_str())
            || self.queue.current().map(|entry| entry.track.id.as_str())
                != Some(data.track_id.as_str())
        {
            return;
        }
        self.lyrics_loading = false;
        self.lyrics_attribution.set_text(data.attribution());
        self.lyrics_attribution.set_visible(true);
        self.lyrics_active = None;
        self.lyrics_data = Some(data.clone());
        self.suppress_lyrics_scroll_for(Duration::from_millis(180));
        match data.kind {
            LyricsKind::Synced => {
                self.lyrics_following = true;
                self.lyrics_follow.set_visible(false);
                self.build_lyric_rows(&data);
                self.lyrics_stack.set_visible_child_name("lines");
                self.update_lyrics_highlight_from_player();
                self.schedule_lyrics_scroll();
            }
            LyricsKind::Plain => {
                self.lyrics_following = false;
                self.lyrics_follow.set_visible(false);
                self.build_lyric_rows(&data);
                self.lyrics_stack.set_visible_child_name("lines");
                self.lyrics_retry.set_visible(false);
            }
            LyricsKind::Instrumental => {
                self.lyrics_following = false;
                self.lyrics_follow.set_visible(false);
                self.lyrics_message.set_text(
                    data.message
                        .as_deref()
                        .unwrap_or("LRCLIB marks this recording as instrumental."),
                );
                self.lyrics_retry.set_visible(false);
                self.lyrics_stack.set_visible_child_name("message");
            }
            LyricsKind::Missing => {
                self.lyrics_following = false;
                self.lyrics_follow.set_visible(false);
                self.lyrics_message.set_text(
                    data.message
                        .as_deref()
                        .unwrap_or("LRCLIB has no lyrics for this recording."),
                );
                self.lyrics_retry.set_visible(true);
                self.lyrics_stack.set_visible_child_name("message");
            }
        }
        if self.lyrics_loading {
            self.lyrics_spinner.start();
        } else {
            self.lyrics_spinner.stop();
        }
    }

    fn lyrics_failed(&mut self, message: &str) {
        if self.closing {
            return;
        }
        self.lyrics_loading = false;
        self.lyrics_data = None;
        self.lyrics_active = None;
        self.lyrics_following = false;
        self.lyrics_follow.set_visible(false);
        self.lyrics_attribution.set_visible(false);
        self.lyrics_spinner.stop();
        self.clear_lyric_rows();
        self.lyrics_message
            .set_text(&format!("Lyrics unavailable. {message}"));
        self.lyrics_retry.set_visible(true);
        self.lyrics_stack.set_visible_child_name("message");
    }

    fn show_lyrics_idle(&mut self) {
        self.lyrics_loading = false;
        self.lyrics_data = None;
        self.lyrics_track_id = None;
        self.lyrics_active = None;
        self.lyrics_following = false;
        self.lyrics_follow.set_visible(false);
        self.lyrics_attribution.set_visible(false);
        self.lyrics_spinner.stop();
        self.clear_lyric_rows();
        self.lyrics_message
            .set_text("Select a recording to see its lyrics.");
        self.lyrics_retry.set_visible(false);
        self.lyrics_stack.set_visible_child_name("message");
    }

    fn show_lyrics_loading(&mut self) {
        self.lyrics_message.set_text("Looking up lyrics on LRCLIB…");
        self.lyrics_retry.set_visible(false);
        self.lyrics_attribution.set_visible(false);
        self.lyrics_follow.set_visible(false);
        self.lyrics_spinner.start();
        self.lyrics_stack.set_visible_child_name("loading");
    }

    fn retry_lyrics(&mut self) {
        let Some(track) = self.queue.current().map(|entry| entry.track.clone()) else {
            self.show_lyrics_idle();
            return;
        };
        self.request_lyrics(&track);
    }

    fn build_lyric_rows(&mut self, data: &LyricsData) {
        self.clear_lyric_rows();
        let timed = data.kind == LyricsKind::Synced;
        for (index, line) in data.lines.iter().enumerate() {
            if timed {
                let button = gtk4::Button::new();
                button.add_css_class("lyric-line");
                button.set_hexpand(true);
                button.set_can_focus(false);
                let text = lyric_label(&line.text, "lyric-text");
                button.set_child(Some(&text));
                let weak = self.self_weak.clone();
                button.connect_clicked(move |_| {
                    if let Some(app) = weak.upgrade() {
                        app.borrow_mut().seek_to_lyric(index);
                    }
                });
                self.lyrics_lines.append(&button);
                self.lyrics_line_widgets.push(button.upcast());
                self.lyrics_line_labels.push(text);
            } else {
                let text = lyric_label(&line.text, "lyric-plain");
                self.lyrics_lines.append(&text);
                self.lyrics_line_widgets.push(text.clone().upcast());
                self.lyrics_line_labels.push(text);
            }
        }
    }

    fn clear_lyric_rows(&mut self) {
        self.cancel_lyrics_scroll();
        while let Some(child) = self.lyrics_lines.first_child() {
            self.lyrics_lines.remove(&child);
        }
        self.lyrics_line_widgets.clear();
        self.lyrics_line_labels.clear();
    }

    fn update_lyrics_highlight_from_player(&mut self) {
        let Some(position) = self.player.as_ref().and_then(Player::position_ms) else {
            return;
        };
        self.update_lyrics_highlight(position);
    }

    fn tick_lyrics(&mut self) {
        if self.closing || !self.side_panel.is_visible() {
            return;
        }
        if !self
            .lyrics_data
            .as_ref()
            .is_some_and(|data| data.kind == LyricsKind::Synced)
        {
            return;
        }
        let Some(position) = self.player.as_ref().and_then(Player::position_ms) else {
            return;
        };
        self.update_lyrics_highlight(position);
    }

    fn update_lyrics_highlight(&mut self, position: u64) {
        let Some(data) = self.lyrics_data.as_ref() else {
            return;
        };
        if data.kind != LyricsKind::Synced {
            return;
        }
        let active = data.active_index(position);
        if active == self.lyrics_active {
            return;
        }
        if let Some(previous) = self.lyrics_active {
            if let Some(label) = self.lyrics_line_labels.get(previous) {
                label.remove_css_class("active");
            }
        }
        self.lyrics_active = active;
        if let Some(index) = active {
            if let Some(label) = self.lyrics_line_labels.get(index) {
                label.add_css_class("active");
            }
            if self.lyrics_following {
                self.scroll_to_active_lyric();
            }
        }
    }

    fn suppress_lyrics_scroll_for(&self, duration: Duration) {
        self.lyrics_suppress_scroll.set(true);
        let suppress = Rc::clone(&self.lyrics_suppress_scroll);
        glib::timeout_add_local_once(duration, move || {
            suppress.set(false);
        });
    }

    fn cancel_lyrics_scroll(&mut self) {
        if let Some(source) = self.lyrics_scroll_animation.take() {
            source.remove();
        }
    }

    fn scroll_to_active_lyric(&mut self) {
        let Some(index) = self.lyrics_active else {
            return;
        };
        let Some(widget) = self.lyrics_line_widgets.get(index) else {
            return;
        };
        let Some(bounds) = widget.compute_bounds(&self.lyrics_lines) else {
            return;
        };
        let adjustment = self.lyrics_scroll.vadjustment();
        let center = f64::from(bounds.y()) + f64::from(bounds.height()) / 2.0;
        let page = adjustment.page_size();
        let lower = adjustment.lower();
        let upper = (adjustment.upper() - page).max(lower);
        let target = (center - page / 2.0).clamp(lower, upper);
        self.cancel_lyrics_scroll();
        let from = adjustment.value();
        let started = Instant::now();
        let weak = self.self_weak.clone();
        self.lyrics_scroll_animation = Some(glib::timeout_add_local(
            Duration::from_millis(16),
            move || {
                let Some(app) = weak.upgrade() else {
                    return glib::ControlFlow::Break;
                };
                let mut app = app.borrow_mut();
                if app.closing
                    || !app.lyrics_following
                    || !app.side_panel.is_visible()
                    || app.side_stack.visible_child_name().as_deref() != Some("lyrics")
                {
                    app.lyrics_scroll_animation = None;
                    return glib::ControlFlow::Break;
                }
                let progress = (started.elapsed().as_secs_f64() / 0.3).min(1.0);
                let eased = progress * progress * (3.0 - 2.0 * progress);
                let previous = app.lyrics_suppress_scroll.replace(true);
                adjustment.set_value(from + (target - from) * eased);
                app.lyrics_suppress_scroll.set(previous);
                if progress >= 1.0 {
                    app.lyrics_scroll_animation = None;
                    glib::ControlFlow::Break
                } else {
                    glib::ControlFlow::Continue
                }
            },
        ));
    }

    fn schedule_lyrics_scroll(&mut self) {
        let weak = self.self_weak.clone();
        glib::idle_add_local_once(move || {
            if let Some(app) = weak.upgrade() {
                app.borrow_mut().scroll_to_active_lyric();
            }
        });
    }

    fn resume_lyrics_following(&mut self) {
        self.lyrics_following = true;
        self.lyrics_follow.set_visible(false);
        self.update_lyrics_highlight_from_player();
        self.scroll_to_active_lyric();
    }

    fn seek_to_lyric(&mut self, index: usize) {
        let Some(line) = self
            .lyrics_data
            .as_ref()
            .and_then(|data| data.lines.get(index))
            .cloned()
        else {
            return;
        };
        let Some(start) = line.start_ms else {
            return;
        };
        let Some(player) = self.player.as_ref() else {
            self.set_status("Start playback before seeking a lyric line.", false);
            return;
        };
        if !self.playback.stream_loaded() {
            self.set_status("Start playback before seeking a lyric line.", false);
            return;
        }
        let fallback = self
            .queue
            .current()
            .map(|entry| entry.track.duration_ms)
            .unwrap_or(0);
        let duration = player.duration_ms().unwrap_or(fallback);
        let target = if duration > 0 {
            start.min(duration.saturating_sub(100))
        } else {
            start
        };
        match player.seek_to_ms(target) {
            Ok(()) => {
                self.set_status("Seeked to the selected lyric line.", false);
                self.tick_lyrics();
            }
            Err(message) => self.set_status(&message, true),
        }
    }

    fn shutdown(&mut self) {
        self.cancel_lyrics_scroll();
        if self.closing {
            return;
        }
        self.closing = true;
        self.resolve_seq = self.resolve_seq.saturating_add(1);
        self.lyrics_seq = 0;
        self.prepared = None;
        self.player = None;
        if let Some(bridge) = self.bridge.as_ref() {
            bridge.shutdown();
        }
        self.artwork_loader.borrow_mut().shutdown();
        self.spinner.stop();
    }
}

fn install_callbacks(
    ui: &Rc<RefCell<AppUi>>,
    rx: mpsc::Receiver<JobResult>,
    player_rx: mpsc::Receiver<PlayerEvent>,
    library_rx: mpsc::Receiver<crate::library::Action>,
) {
    let weak = Rc::downgrade(ui);
    ui.borrow().search_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().start_search();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().search_entry.connect_activate(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().start_search();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().home_search_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().navigate("search");
        }
    });

    for (button, page) in [
        (ui.borrow().nav_library.clone(), "library"),
        (ui.borrow().nav_home.clone(), "home"),
        (ui.borrow().nav_search.clone(), "search"),
    ] {
        let weak = Rc::downgrade(ui);
        button.connect_clicked(move |_| {
            if let Some(app) = weak.upgrade() {
                app.borrow_mut().navigate(page);
            }
        });
    }

    let weak = Rc::downgrade(ui);
    ui.borrow().nav_queue.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().toggle_panel("queue");
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().side_close.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().close_side_panel();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow()
        .results_list
        .connect_row_activated(move |_, row| {
            let Some(app) = weak.upgrade() else {
                return;
            };
            let index = row.index().max(0) as usize;
            app.borrow_mut().activate_search_result(index);
        });

    let weak = Rc::downgrade(ui);
    ui.borrow().queue_list.connect_row_activated(move |_, row| {
        let Some(app) = weak.upgrade() else {
            return;
        };
        let index = row.index().max(0) as usize;
        let entry_id = {
            let app_ref = app.borrow();
            app_ref.queue.upcoming().nth(index).map(|entry| entry.id)
        };
        if let Some(id) = entry_id {
            app.borrow_mut().play_queue_entry(id);
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().play_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().toggle_play();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().stop_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().stop_playback();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().previous_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().previous_track();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().next_button.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().next_track();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow()
        .volume_scale
        .connect_value_changed(move |scale| {
            if let Some(app) = weak.upgrade() {
                app.borrow_mut().set_volume(scale.value());
            }
        });

    let weak = Rc::downgrade(ui);
    ui.borrow().queue_toggle.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().toggle_panel("queue");
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().lyrics_toggle.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().toggle_panel("lyrics");
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().queue_clear.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().queue_clear();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().lyrics_retry.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().retry_lyrics();
        }
    });

    let weak = Rc::downgrade(ui);
    ui.borrow().lyrics_follow.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().resume_lyrics_following();
        }
    });

    let suppress = Rc::clone(&ui.borrow().progress_suppress);
    let weak = Rc::downgrade(ui);
    ui.borrow()
        .progress_scale
        .connect_value_changed(move |scale| {
            if suppress.get() {
                return;
            }
            if let Some(app) = weak.upgrade() {
                app.borrow_mut()
                    .seek_to_progress(scale.value() / PROGRESS_MAX);
            }
        });

    let scroll = gtk4::EventControllerScroll::new(gtk4::EventControllerScrollFlags::VERTICAL);
    let weak = Rc::downgrade(ui);
    scroll.connect_scroll(move |_, _, _| {
        if let Some(app) = weak.upgrade() {
            let mut app = app.borrow_mut();
            app.cancel_lyrics_scroll();
            app.lyrics_following = false;
            app.lyrics_follow.set_visible(
                app.lyrics_data
                    .as_ref()
                    .is_some_and(|data| data.kind == LyricsKind::Synced),
            );
        }
        glib::Propagation::Proceed
    });
    ui.borrow().lyrics_scroll.add_controller(scroll);

    let suppress = Rc::clone(&ui.borrow().lyrics_suppress_scroll);
    let weak = Rc::downgrade(ui);
    ui.borrow()
        .lyrics_scroll
        .vadjustment()
        .connect_value_changed(move |_| {
            if suppress.get() {
                return;
            }
            let Some(app) = weak.upgrade() else {
                return;
            };
            let mut app = app.borrow_mut();
            if app.lyrics_following
                && app.side_panel.is_visible()
                && app
                    .lyrics_data
                    .as_ref()
                    .is_some_and(|data| data.kind == LyricsKind::Synced)
            {
                app.cancel_lyrics_scroll();
                app.lyrics_following = false;
                app.lyrics_follow.set_visible(true);
            }
        });

    let weak = Rc::downgrade(ui);
    ui.borrow().window.connect_is_active_notify(move |window| {
        if window.is_active() {
            if let Some(app) = weak.upgrade() {
                let Ok(app) = app.try_borrow() else {
                    return;
                };
                if app.main_stack.visible_child_name().as_deref() == Some("library") {
                    crate::library::LibraryPane::request(
                        &app.library,
                        serde_json::json!({"operation":"refresh"}),
                    );
                }
            }
        }
    });
    let weak = Rc::downgrade(ui);
    ui.borrow().window.connect_close_request(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().shutdown();
        }
        glib::Propagation::Proceed
    });

    // The GLib source owns the UI until close; other callbacks remain weak.
    let app = Rc::clone(ui);
    glib::timeout_add_local(Duration::from_millis(40), move || {
        while let Ok(action) = library_rx.try_recv() {
            let mut ui = app.borrow_mut();
            match action {
                crate::library::Action::Request(command) => {
                    match ui
                        .bridge
                        .as_ref()
                        .map(|bridge| bridge.submit_library(command))
                    {
                        Some(Ok(seq)) => ui.library_seq = seq,
                        _ => crate::library::LibraryPane::apply(
                            &ui.library,
                            Err("Music bridge unavailable.".into()),
                        ),
                    }
                }
                crate::library::Action::Play(tracks, index) => {
                    // Start the selected occurrence, preserving duplicate song positions.
                    if let Some(entry) = ui.queue.replace_search(&tracks, index) {
                        ui.remember_selected(&entry.track);
                        ui.refresh_queue_view();
                        ui.begin_entry(&entry, true);
                    }
                }
                crate::library::Action::Queue(track, next) => {
                    if next {
                        ui.queue_play_next(track);
                    } else {
                        ui.queue_add(track);
                    }
                }
            }
        }
        while let Ok(result) = rx.try_recv() {
            app.borrow_mut().handle_job(result);
        }
        while let Ok(event) = player_rx.try_recv() {
            app.borrow_mut().handle_player_event(event);
        }
        app.borrow_mut().tick_lyrics();
        app.borrow_mut().tick_progress();
        if app.borrow().closing {
            glib::ControlFlow::Break
        } else {
            glib::ControlFlow::Continue
        }
    });
}

fn track_row(track: &Track, weak: &Weak<RefCell<AppUi>>) -> (gtk4::ListBoxRow, ArtworkSlot) {
    let row = gtk4::ListBoxRow::new();
    row.add_css_class("track-row");
    row.set_activatable(true);
    row.set_tooltip_text(Some(&format!("Play {}", track.name)));

    let outer = gtk4::Box::new(gtk4::Orientation::Horizontal, 12);
    outer.set_margin_top(5);
    outer.set_margin_bottom(5);
    outer.set_margin_start(10);
    outer.set_margin_end(8);

    let (artwork_frame, artwork_picture) = artwork_view(38, 15);
    let artwork_slot = ArtworkSlot::new(&artwork_picture);

    let text = gtk4::Box::new(gtk4::Orientation::Vertical, 2);
    text.set_hexpand(true);
    let title = label(&track.name, "row-title");
    title.set_ellipsize(pango::EllipsizeMode::End);
    title.set_max_width_chars(70);
    let artist = label(&track.artist_line(), "row-artist");
    artist.set_ellipsize(pango::EllipsizeMode::End);
    artist.set_max_width_chars(70);
    text.append(&title);
    text.append(&artist);

    let duration = label(&track.duration_text(), "row-duration");
    duration.set_valign(gtk4::Align::Center);
    let play = gtk4::Image::from_icon_name("media-playback-start-symbolic");
    play.set_pixel_size(15);
    play.add_css_class("row-play");

    let menu_button = gtk4::MenuButton::new();
    let menu_icon = gtk4::Image::from_icon_name("view-more-symbolic");
    menu_icon.set_pixel_size(14);
    menu_button.set_child(Some(&menu_icon));
    menu_button.add_css_class("row-menu");
    menu_button.set_tooltip_text(Some("Queue options"));

    let popover = gtk4::Popover::new();
    popover.add_css_class("track-menu");
    let choices = gtk4::Box::new(gtk4::Orientation::Vertical, 2);
    choices.set_margin_top(5);
    choices.set_margin_bottom(5);
    choices.set_margin_start(5);
    choices.set_margin_end(5);
    let play_next = gtk4::Button::with_label("Play next");
    play_next.add_css_class("menu-action");
    let add_queue = gtk4::Button::with_label("Add to queue");
    add_queue.add_css_class("menu-action");
    choices.append(&play_next);
    choices.append(&add_queue);
    let add_playlist = gtk4::Button::with_label("Add to playlist");
    add_playlist.add_css_class("menu-action");
    choices.append(&add_playlist);
    let playlist_weak = weak.clone();
    let playlist_track = track.clone();
    let playlist_popover = popover.clone();
    add_playlist.connect_clicked(move |_| {
        playlist_popover.popdown();
        if let Some(app) = playlist_weak.upgrade() {
            crate::library::LibraryPane::add_dialog(&app.borrow().library, &playlist_track);
        }
    });
    popover.set_child(Some(&choices));
    menu_button.set_popover(Some(&popover));

    let next_weak = weak.clone();
    let next_popover = popover.clone();
    let next_track = track.clone();
    play_next.connect_clicked(move |_| {
        if let Some(app) = next_weak.upgrade() {
            app.borrow_mut().queue_play_next(next_track.clone());
        }
        next_popover.popdown();
    });
    let add_weak = weak.clone();
    let add_popover = popover;
    let add_track = track.clone();
    add_queue.connect_clicked(move |_| {
        if let Some(app) = add_weak.upgrade() {
            app.borrow_mut().queue_add(add_track.clone());
        }
        add_popover.popdown();
    });

    outer.append(&artwork_frame);
    outer.append(&text);
    outer.append(&duration);
    outer.append(&play);
    outer.append(&menu_button);
    row.set_child(Some(&outer));
    (row, artwork_slot)
}

fn recent_card(
    track: &Track,
    index: usize,
    weak: &Weak<RefCell<AppUi>>,
) -> (gtk4::Button, ArtworkSlot) {
    let card = gtk4::Button::new();
    card.add_css_class("recent-card");
    card.set_size_request(HOME_CARD_MIN_WIDTH, -1);
    card.set_hexpand(true);
    card.set_halign(gtk4::Align::Fill);
    card.set_tooltip_text(Some(&format!("Play {}", track.name)));
    let content = gtk4::Box::new(gtk4::Orientation::Vertical, 7);
    content.set_hexpand(true);
    content.set_halign(gtk4::Align::Fill);
    let (artwork_frame, artwork_picture) = fill_artwork(24);
    content.append(&artwork_frame);
    let title = label(&track.name, "card-title");
    title.set_ellipsize(pango::EllipsizeMode::End);
    title.set_max_width_chars(24);
    let artist = label(&track.artist_line(), "card-artist");
    artist.set_ellipsize(pango::EllipsizeMode::End);
    artist.set_max_width_chars(24);
    content.append(&title);
    content.append(&artist);
    card.set_child(Some(&content));
    let weak = weak.clone();
    card.connect_clicked(move |_| {
        if let Some(app) = weak.upgrade() {
            app.borrow_mut().activate_recent(index);
        }
    });
    let slot = ArtworkSlot::new(&artwork_picture);
    (card, slot)
}

fn fill_artwork(glyph_size: i32) -> (SquareArtwork, gtk4::Picture) {
    let (frame, picture) = artwork_view(HOME_CARD_MIN_WIDTH, glyph_size);
    frame.set_halign(gtk4::Align::Fill);
    frame.set_valign(gtk4::Align::Start);
    picture.set_size_request(HOME_CARD_MIN_WIDTH, HOME_CARD_MIN_WIDTH);
    // Ask GTK for height equal to allocated width, not only a minimum square size.
    let square: SquareArtwork = glib::Object::new();
    square.set_halign(gtk4::Align::Fill);
    square.set_valign(gtk4::Align::Start);
    frame.set_valign(gtk4::Align::Fill);
    frame.set_vexpand(true);
    frame.set_parent(&square);
    (square, picture)
}

fn queue_row(
    entry: &QueueEntry,
    position: usize,
    can_up: bool,
    can_down: bool,
    weak: &Weak<RefCell<AppUi>>,
) -> gtk4::ListBoxRow {
    let row = gtk4::ListBoxRow::new();
    row.add_css_class("queue-row");
    row.set_activatable(true);
    row.set_tooltip_text(Some(&format!("Play {}", entry.track.name)));

    let outer = gtk4::Box::new(gtk4::Orientation::Horizontal, 7);
    outer.set_margin_top(6);
    outer.set_margin_bottom(6);
    outer.set_margin_start(8);
    outer.set_margin_end(6);

    let index = label(&format!("{position:02}"), "queue-index");
    index.set_valign(gtk4::Align::Center);
    let text = gtk4::Box::new(gtk4::Orientation::Vertical, 1);
    text.set_hexpand(true);
    let title = label(&entry.track.name, "row-title");
    title.set_ellipsize(pango::EllipsizeMode::End);
    title.set_max_width_chars(28);
    let artist = label(&entry.track.artist_line(), "row-artist");
    artist.set_ellipsize(pango::EllipsizeMode::End);
    artist.set_max_width_chars(28);
    text.append(&title);
    text.append(&artist);

    let controls = gtk4::Box::new(gtk4::Orientation::Horizontal, 1);
    controls.set_valign(gtk4::Align::Center);
    let up = small_icon_button("go-up-symbolic", "Move up");
    up.set_sensitive(can_up);
    let down = small_icon_button("go-down-symbolic", "Move down");
    down.set_sensitive(can_down);
    let remove = small_icon_button("window-close-symbolic", "Remove from queue");
    controls.append(&up);
    controls.append(&down);
    controls.append(&remove);

    let id = entry.id;
    let up_weak = weak.clone();
    up.connect_clicked(move |_| {
        if let Some(app) = up_weak.upgrade() {
            app.borrow_mut().queue_move_up(id);
        }
    });
    let down_weak = weak.clone();
    down.connect_clicked(move |_| {
        if let Some(app) = down_weak.upgrade() {
            app.borrow_mut().queue_move_down(id);
        }
    });
    let remove_weak = weak.clone();
    remove.connect_clicked(move |_| {
        if let Some(app) = remove_weak.upgrade() {
            app.borrow_mut().queue_remove(id);
        }
    });

    outer.append(&index);
    outer.append(&text);
    outer.append(&controls);
    row.set_child(Some(&outer));
    row
}

fn lyric_label(text: &str, class: &str) -> gtk4::Label {
    let label = gtk4::Label::new(Some(text));
    label.set_xalign(0.0);
    label.set_wrap(true);
    label.set_wrap_mode(pango::WrapMode::WordChar);
    label.set_justify(gtk4::Justification::Left);
    label.set_max_width_chars(42);
    label.set_hexpand(true);
    label.add_css_class(class);
    label
}

fn transport_icon_button(icon: &str, tooltip: &str) -> gtk4::Button {
    let button = gtk4::Button::new();
    let image = gtk4::Image::from_icon_name(icon);
    image.set_pixel_size(16);
    button.set_child(Some(&image));
    button.add_css_class("transport-button");
    button.set_tooltip_text(Some(tooltip));
    set_accessible_label(&button, tooltip);
    button
}

fn small_icon_button(icon: &str, tooltip: &str) -> gtk4::Button {
    let button = gtk4::Button::new();
    let image = gtk4::Image::from_icon_name(icon);
    image.set_pixel_size(12);
    button.set_child(Some(&image));
    button.add_css_class("queue-control");
    button.set_tooltip_text(Some(tooltip));
    set_accessible_label(&button, tooltip);
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

fn noun<'a>(count: usize, singular: &'a str, plural: &'a str) -> &'a str {
    if count == 1 { singular } else { plural }
}

fn set_active(button: &gtk4::Button, active: bool) {
    if active {
        button.add_css_class("active");
    } else {
        button.remove_css_class("active");
    }
}

fn format_time(milliseconds: u64) -> String {
    let seconds = milliseconds / 1000;
    format!("{}:{:02}", seconds / 60, seconds % 60)
}

fn seek_target(fraction: f64, duration_ms: u64) -> Option<u64> {
    if duration_ms == 0 || !fraction.is_finite() {
        return None;
    }
    let target = (fraction.clamp(0.0, 1.0) * duration_ms as f64).round() as u64;
    Some(target.min(duration_ms.saturating_sub(100)))
}

#[cfg(test)]
mod layout_tests {
    use super::*;

    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn home_artwork_stays_square_after_hidden_population_and_resize() {
        gtk4::init().expect("GTK display");
        let home = build_home_page();
        let pages = gtk4::Stack::new();
        pages.add_named(&gtk4::Label::new(Some("Search")), Some("search"));
        pages.add_named(&home.page, Some("home"));
        pages.set_visible_child_name("search");
        let body = gtk4::Box::new(gtk4::Orientation::Horizontal, 0);
        body.append(&build_sidebar().container);
        pages.set_hexpand(true);
        body.append(&pages);
        body.set_vexpand(true);
        let root = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
        root.append(&build_player_bar().header);
        root.append(&body);
        let window = gtk4::Window::builder().child(&root).build();
        window.set_size_request(780, 580);
        let track = Track {
            id: "dm4tkSNKfFI".into(),
            uri: "https://music.youtube.com/watch?v=dm4tkSNKfFI".into(),
            name: "A long recording title for allocation verification".into(),
            artists: vec!["Test artist".into()],
            album: String::new(),
            duration_ms: 1000,
            artwork_url: None,
        };
        for index in 0..10 {
            let (card, _) = recent_card(&track, index, &Weak::new());
            home.recent_flow.insert(&card, -1);
        }
        home.stack.set_visible_child_name("recent");
        pages.set_visible_child_name("home");
        window.present();
        for (width, height) in [(1180, 760), (780, 580), (1180, 760)] {
            window.set_default_size(width, height);
            let until = Instant::now() + Duration::from_millis(250);
            while Instant::now() < until {
                while glib::MainContext::default().pending() {
                    glib::MainContext::default().iteration(false);
                }
                std::thread::sleep(Duration::from_millis(5));
            }
            let row = home
                .recent_flow
                .first_child()
                .unwrap()
                .downcast::<gtk4::FlowBoxChild>()
                .unwrap();
            let card = row.child().unwrap();
            let square = card
                .first_child()
                .unwrap()
                .first_child()
                .unwrap()
                .downcast::<SquareArtwork>()
                .unwrap();
            let artwork = square.first_child().unwrap();
            assert!(artwork.width() >= HOME_CARD_MIN_WIDTH);
            assert!(
                artwork.width() >= card.width() - 2,
                "artwork must fill card width"
            );
            assert!(
                (artwork.width() - artwork.height()).abs() <= 1,
                "artwork {}x{} at window width {width}",
                artwork.width(),
                artwork.height()
            );
            println!(
                "window width {width}: artwork {}x{}",
                artwork.width(),
                artwork.height()
            );
        }
        window.close();
    }

    #[test]
    fn progress_seek_targets_are_clamped() {
        assert_eq!(seek_target(0.5, 200_000), Some(100_000));
        assert_eq!(seek_target(-1.0, 200_000), Some(0));
        assert_eq!(seek_target(2.0, 200_000), Some(199_900));
        assert_eq!(seek_target(f64::NAN, 200_000), None);
        assert_eq!(seek_target(0.5, 0), None);
    }
}
