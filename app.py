#!/usr/bin/env python3
"""Ember: a native GTK player with a separate, owned audio engine."""
import html
import json
import queue
from pathlib import Path
import random
import subprocess
import threading
import time
import urllib.parse
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk, Pango

from lyrics import demo_lines, fetch_lyrics, import_lyrics, metadata_lyrics
from music import search as music_search, resolve as music_resolve
from audio import Audio
from services import Spotify, DATA, load_private, save_private
from mpris import Mpris

ROOT = Path(__file__).resolve().parent
ARTWORK_HOSTS = {"i1.sndcdn.com", "i2.sndcdn.com", "i3.sndcdn.com", "i4.sndcdn.com", "i.scdn.co", "mosaic.scdn.co", "image-cdn-ak.spotifycdn.com", "image-cdn-fa.spotifycdn.com"}


def label(text, css=None, wrap=False):
    widget = Gtk.Label(label=text, xalign=0)
    if wrap:
        widget.set_wrap(True)
        widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if css:
        widget.add_css_class(css)
    return widget


def icon(name, size=16, css=None):
    widget = Gtk.Image.new_from_icon_name(name)
    widget.set_pixel_size(size)
    if css:
        widget.add_css_class(css)
    return widget


def icon_button(name, tooltip, callback, size=16, css=None):
    widget = Gtk.Button()
    widget.set_child(icon(name, size))
    widget.set_tooltip_text(tooltip)
    widget.add_css_class("icon-button")
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda *_: callback())
    return widget


def toggle_button(name, tooltip, callback):
    widget = Gtk.ToggleButton()
    widget.set_child(icon(name, 15))
    widget.set_tooltip_text(tooltip)
    widget.add_css_class("icon-button")
    widget.connect("toggled", lambda *_: callback())
    return widget


def button(text, callback, css=None):
    widget = Gtk.Button(label=text)
    widget.connect("clicked", lambda *_: callback())
    if css:
        widget.add_css_class(css)
    return widget


def clear(box):
    while child := box.get_first_child():
        box.remove(child)


def timecode(value):
    value = max(0, int(value))
    return f"{value // 60}:{value % 60:02d}"


def best_image(images, target=320):
    images = [image for image in images or [] if image.get("url")]
    if not images:
        return None
    return min(images, key=lambda image: abs(image.get("width", 0) - target)).get("url")


class ArtCanvas(Gtk.Widget):
    __gtype_name__ = "EmberArtCanvas"

    def __init__(self, size, radius, background):
        super().__init__()
        self.size = size
        self.radius = radius
        self.texture = None
        self.background = Gdk.RGBA()
        self.background.parse(background)
        self.set_size_request(size, size)

    def do_measure(self, orientation, for_size):
        return (self.size, self.size, -1, -1)

    def do_snapshot(self, snapshot):
        width, height = self.get_width(), self.get_height()
        bounds = Graphene.Rect().init(0, 0, width, height)
        if self.radius:
            rounded = Gsk.RoundedRect()
            rounded.init_from_rect(bounds, min(self.radius, width / 2, height / 2))
            snapshot.push_rounded_clip(rounded)
        if self.texture:
            snapshot.append_texture(self.texture, bounds)
        else:
            snapshot.append_color(self.background, bounds)
        if self.radius:
            snapshot.pop()


class Artwork(Gtk.Overlay):
    def __init__(self, size=160, radius=8, placeholder="music-note-symbolic", background="#1c1c20"):
        super().__init__()
        self.size = size
        self.canvas = ArtCanvas(size, radius, background)
        self.set_child(self.canvas)
        self.placeholder = icon(placeholder, max(16, min(28, size // 4)), "art-placeholder")
        self.placeholder.set_halign(Gtk.Align.CENTER)
        self.placeholder.set_valign(Gtk.Align.CENTER)
        self.placeholder.set_can_target(False)
        self.add_overlay(self.placeholder)
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

    def set_texture(self, texture):
        self.canvas.texture = texture
        self.canvas.queue_draw()
        self.placeholder.set_visible(False)

    def clear_art(self):
        self.canvas.texture = None
        self.canvas.queue_draw()
        self.placeholder.set_visible(True)


class Ember(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="io.ember.Player")
        self.spotify = Spotify()
        self.saved_tracks = load_private(DATA / "youtube-library.json").get("tracks", [])
        self.audio = Audio(self.audio_event)
        self.pending_track = False
        self.engine = None
        self.engine_ready = False
        self.playing = False
        self.demo = False
        self.lines = []
        self.lyric_labels = []
        self.active_line = -1
        self.active_lines = set()
        self.queue = []
        self.queue_index = -1
        self.track = None
        self.position = 0
        self.duration = 0
        self.anchor = time.monotonic()
        self.request_id = 0
        self.library_request = 0
        self.shuffle = False
        self.repeat = False
        self.login_pending = False
        self.closed = False
        self.account_generation = 0
        self.engine_generation = 0
        self.account_name = None
        self.visible_tracks = []
        self.library_source = "search"
        self.last_nav = None
        self.active_playlist = None
        self.playlist_count = 0
        self.playlist_rows = {}
        self.current_art_url = None
        self.animations = True
        self.art_cache = {}
        self.art_waiters = {}
        self.art_queued = set()
        self.art_queue = queue.Queue()
        threading.Thread(target=self.art_worker, daemon=True).start()
        self.connect("activate", self.activate)

    def task(self, work, done, failed=None):
        generation = self.account_generation
        def run():
            try:
                result = work()
            except Exception as error:
                message = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Operation failed. Check your connection and setup, then retry."
                GLib.idle_add(self.deliver, generation, failed or self.status, message)
            else:
                GLib.idle_add(self.deliver, generation, done, result)
        threading.Thread(target=run, daemon=True).start()

    def deliver(self, generation, callback, result):
        if not self.closed and generation == self.account_generation:
            callback(result)
        return False

    def activate(self, *_):
        if hasattr(self, "window"):
            self.window.present()
            return
        settings = Gtk.Settings.get_default()
        if settings.find_property("gtk-interface-color-scheme"):
            settings.set_property("gtk-interface-color-scheme", 2)
        else:
            settings.set_property("gtk-application-prefer-dark-theme", True)
        self.animations = settings.get_property("gtk-enable-animations")
        display = Gdk.Display.get_default()
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ROOT / "icons"))
        provider = Gtk.CssProvider()
        provider.load_from_path(str(ROOT / "style.css"))
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window = Gtk.ApplicationWindow(application=self, title="Ember", default_width=1280, default_height=820)
        self.window.set_size_request(980, 640)
        self.window.connect("close-request", self.close)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.window.set_child(outer)
        self.window.set_titlebar(self.build_header())
        content = Gtk.Box(spacing=0, vexpand=True)
        outer.append(content)
        content.append(self.build_sidebar())
        self.pages = Gtk.Stack(hexpand=True, vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=160 if self.animations else 0)
        self.pages.add_named(self.build_home(), "home")
        self.pages.add_named(self.build_library(), "library")
        self.pages.add_named(self.build_lyrics(), "lyrics")
        content.append(self.pages)
        outer.append(self.build_player())
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self.key_pressed)
        self.window.add_controller(keys)
        self.reset_player()
        self.set_account_state("YouTube Music")
        self.show_page("home")
        self.status("Search YouTube Music or paste a public track link · no login required")
        self.window.present()
        try:
            self.mpris = Mpris(self)
        except GLib.Error:
            self.mpris = None
            self.status("Desktop media controls unavailable; in-app controls still work.")
        GLib.timeout_add(50, self.tick)

    def show_page(self, name):
        self.pages.set_visible_child_name(name)
        if name in ("home", "lyrics"):
            self.set_active(name)

    def set_active(self, nav=None, playlist=None):
        for widget in self.nav_buttons.values():
            widget.remove_css_class("active")
        if nav in self.nav_buttons:
            self.nav_buttons[nav].add_css_class("active")
        if self.active_playlist is not None and self.active_playlist is not playlist:
            self.active_playlist.remove_css_class("active")
        self.active_playlist = playlist
        if playlist is not None:
            playlist.add_css_class("active")

    def open_search(self):
        self.library_request += 1
        self.pages.set_visible_child_name("library")
        self.library_source = "search"
        self.last_nav = "search"
        self.set_active("search")
        self.library_title.set_text("Search")
        self.library_meta.set_text("Songs, artists, or a YouTube Music track or playlist link")
        clear(self.track_list)
        self.visible_tracks = []
        self.next_path = None
        self.more.set_visible(False)
        if self.account_name:
            self.show_empty("system-search-symbolic", "Search YouTube Music", "Results appear here. Paste a track link to play it directly.")
            self.search.grab_focus()
        else:
            self.show_empty("system-search-symbolic", "Sign in to search Spotify", "Connect your account to search songs and open your library.")

    def show_empty(self, icon_name, title, detail):
        self.empty_icon.set_from_icon_name(icon_name)
        self.empty_title.set_text(title)
        self.empty_detail.set_text(detail)
        self.empty_state.set_visible(True)

    def hide_empty(self):
        self.empty_state.set_visible(False)

    def build_header(self):
        header = Gtk.HeaderBar()
        header.add_css_class("app-header")
        brand = Gtk.Box(spacing=8)
        brand.append(icon("music-note-symbolic", 16, "brand-icon"))
        brand.append(label("Ember", "brand"))
        header.pack_start(brand)
        header.set_title_widget(Gtk.Box())
        tools = Gtk.Box(spacing=4)
        self.account_button = Gtk.Button()
        self.account_button.add_css_class("account-button")
        self.account_button.connect("clicked", lambda *_: self.open_search())
        account = Gtk.Box(spacing=7)
        account.append(icon("avatar-default-symbolic", 15))
        self.account_label = label("YouTube Music", "account-label")
        self.account_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.account_label.set_max_width_chars(22)
        account.append(self.account_label)
        self.account_button.set_child(account)
        tools.append(self.account_button)
        tools.append(icon_button("emblem-system-symbolic", "Settings", self.settings))
        header.pack_end(tools)
        return header

    def build_sidebar(self):
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        side.add_css_class("sidebar")
        side.set_size_request(232, -1)
        nav = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        nav.add_css_class("nav")
        side.append(nav)
        self.nav_buttons = {}
        entries = (
            ("home", "Home", "go-home-symbolic", lambda: self.show_page("home")),
            ("search", "Search", "system-search-symbolic", self.open_search),
            ("liked", "Liked Songs", "heart-symbolic", self.open_liked),
            ("lyrics", "Lyrics", "format-justify-left-symbolic", lambda: self.show_page("lyrics")),
        )
        for key, text, icon_name, callback in entries:
            widget = Gtk.Button()
            widget.add_css_class("nav-button")
            row = Gtk.Box(spacing=11)
            row.append(icon(icon_name, 16, "nav-icon"))
            row.append(label(text, "nav-label"))
            widget.set_child(row)
            widget.connect("clicked", lambda *_, callback=callback: callback())
            nav.append(widget)
            self.nav_buttons[key] = widget
        side.append(label("Playlists", "sidebar-eyebrow"))
        self.playlist_hint = label("Paste a public YouTube Music playlist link in Search.", "sidebar-hint", wrap=True)
        self.playlists = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        area.add_css_class("playlist-area")
        area.append(self.playlist_hint)
        area.append(self.playlists)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("sidebar-scroll")
        scroll.set_child(area)
        side.append(scroll)
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        footer.add_css_class("sidebar-footer")
        self.message = label("", "status", wrap=True)
        self.message.set_max_width_chars(28)
        footer.append(self.message)
        footer.append(label("YouTube Music · native playback", "footer-note"))
        side.append(footer)
        return side

    def build_home(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("page-scroll")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        body.add_css_class("home")
        body.append(label("Home", "page-title"))
        self.home_guest = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.home_guest.add_css_class("guest-panel")
        self.home_guest.append(label("YouTube Music, played here.", "hero-title", wrap=True))
        copy = label("Search YouTube Music songs or paste a track or playlist link. Public playback needs no developer key. Restricted tracks may be unavailable.", "hero-copy", wrap=True)
        copy.set_max_width_chars(56)
        self.home_guest.append(copy)
        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(4)
        actions.append(button("Search YouTube Music", self.open_search, "accent"))
        actions.append(button("Silent lyrics preview", self.start_demo, "ghost-button"))
        self.home_guest.append(actions)
        body.append(self.home_guest)
        self.guest_shelf = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.guest_shelf.append(label("Your playlists", "shelf-heading"))
        empty_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        empty_panel.add_css_class("guest-empty")
        empty_icon = icon("music-note-symbolic", 22, "empty-icon")
        empty_icon.set_halign(Gtk.Align.CENTER)
        empty_panel.append(empty_icon)
        empty_title = label("Nothing here yet", "empty-title")
        empty_title.set_xalign(0.5)
        empty_panel.append(empty_title)
        empty_detail = label("Open public YouTube Music playlists by pasting their link into Search.", "empty-detail", wrap=True)
        empty_detail.set_xalign(0.5)
        empty_detail.set_justify(Gtk.Justification.CENTER)
        empty_detail.set_max_width_chars(62)
        empty_panel.append(empty_detail)
        self.guest_shelf.append(empty_panel)
        body.append(self.guest_shelf)
        self.home_user = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.home_user.add_css_class("home-user")
        self.home_user.set_visible(False)
        self.home_user.append(self.liked_banner())
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading.append(label("Your playlists", "shelf-heading"))
        self.home_user.append(heading)
        self.home_playlists = Gtk.Box(spacing=18)
        self.home_playlists.add_css_class("shelf")
        self.home_playlists.set_halign(Gtk.Align.START)
        self.home_shelf = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, min_content_height=216)
        self.home_shelf.add_css_class("shelf-scroller")
        self.home_shelf.set_child(self.home_playlists)
        self.home_shelf.set_visible(False)
        self.home_user.append(self.home_shelf)
        self.home_empty = label("Paste a public YouTube Music playlist link into Search to browse it.", "empty-inline", wrap=True)
        self.home_empty.set_visible(False)
        self.home_user.append(self.home_empty)
        body.append(self.home_user)
        scroll.set_child(body)
        return scroll

    def liked_banner(self):
        banner = Gtk.Button()
        banner.add_css_class("library-banner")
        banner.set_tooltip_text("Open Liked Songs")
        content = Gtk.Box(spacing=14)
        art = Artwork(52, 6, "heart-symbolic", "#291a1e")
        art.add_css_class("accent-art")
        content.append(art)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER, hexpand=True)
        text.append(label("Liked Songs", "banner-title"))
        text.append(label("Your saved tracks", "banner-sub"))
        content.append(text)
        content.append(icon("go-next-symbolic", 14, "chevron"))
        banner.set_child(content)
        banner.connect("clicked", lambda *_: self.open_liked())
        return banner

    def shelf_card(self, title, subtitle, callback, images=None):
        card = Gtk.Button()
        card.add_css_class("shelf-card")
        card.set_tooltip_text(title)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        art = Artwork(160, 8, "music-note-symbolic")
        cover = Gtk.Overlay()
        cover.set_child(art)
        hover = Gtk.Box()
        hover.add_css_class("hover-play")
        hover.append(icon("media-playback-start-symbolic", 14))
        hover.set_halign(Gtk.Align.END)
        hover.set_valign(Gtk.Align.END)
        hover.set_margin_end(9)
        hover.set_margin_bottom(9)
        hover.set_can_target(False)
        hover.set_visible(False)
        cover.add_overlay(hover)
        motion = Gtk.EventControllerMotion()
        motion.connect("enter", lambda *_: hover.set_visible(True))
        motion.connect("leave", lambda *_: hover.set_visible(False))
        card.add_controller(motion)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_: hover.set_visible(True))
        focus.connect("leave", lambda *_: hover.set_visible(False))
        card.add_controller(focus)
        content.append(cover)
        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        caption.add_css_class("shelf-caption")
        name = label(title, "shelf-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(22)
        detail = label(subtitle, "shelf-sub")
        detail.set_ellipsize(Pango.EllipsizeMode.END)
        detail.set_max_width_chars(22)
        caption.append(name)
        caption.append(detail)
        content.append(caption)
        card.set_child(content)
        card.connect("clicked", lambda *_: callback())
        self.load_artwork(best_image(images, 400), self.art_callback(art))
        return card

    def playlist_row(self, item):
        row = Gtk.Button()
        row.add_css_class("playlist-row")
        row.set_tooltip_text(item.get("name", "Playlist"))
        content = Gtk.Box(spacing=9)
        art = Artwork(24, 4, "music-note-symbolic")
        art.add_css_class("row-art")
        content.append(art)
        name = label(item.get("name", "Playlist"), "playlist-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(21)
        content.append(name)
        row.set_child(content)
        row.connect("clicked", lambda *_: self.open_playlist(item))
        self.playlist_rows[item["id"]] = row
        self.load_artwork(best_image(item.get("images") or [], 64), self.art_callback(art))
        return row

    def open_playlist(self, item):
        self.load_tracks(item["uri"], item.get("name", "Playlist"), nav=self.playlist_rows.get(item.get("id")))

    def open_liked(self):
        if not self.account_name:
            self.pages.set_visible_child_name("library")
            self.library_source = "liked"
            self.set_active("liked")
            self.library_title.set_text("Liked Songs")
            self.library_meta.set_text("Your saved tracks")
            clear(self.track_list)
            self.visible_tracks = []
            self.next_path = None
            self.more.set_visible(False)
            self.show_empty("heart-symbolic", "Sign in to see Liked Songs", "Connect your Spotify account to load your saved tracks.")
            return
        self.load_tracks("me/tracks", "Liked Songs", nav="liked")

    def art_worker(self):
        while True:
            url = self.art_queue.get()
            data = None
            try:
                with urllib.request.urlopen(url, timeout=10) as response:
                    data = response.read(2_000_001)
                if len(data) > 2_000_000:
                    data = None
            except Exception:
                data = None
            GLib.idle_add(self.art_delivered, url, data)

    def art_delivered(self, url, data):
        if self.closed:
            return False
        callbacks = self.art_waiters.pop(url, [])
        self.art_queued.discard(url)
        texture = None
        if data:
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except GLib.Error:
                texture = None
        self.art_cache[url] = texture
        while len(self.art_cache) > 80:
            del self.art_cache[next(iter(self.art_cache))]
        for callback in callbacks:
            if texture is not None:
                callback(texture)
        return False

    def load_artwork(self, url, callback):
        parsed = urllib.parse.urlsplit(url or "")
        if parsed.scheme != "https" or parsed.hostname not in ARTWORK_HOSTS:
            return
        if url in self.art_cache:
            if self.art_cache[url] is not None:
                callback(self.art_cache[url])
            return
        self.art_waiters.setdefault(url, []).append(callback)
        if url not in self.art_queued:
            self.art_queued.add(url)
            self.art_queue.put(url)

    def art_callback(self, widget):
        def apply(texture):
            if widget.get_root() is not None:
                widget.set_texture(texture)
        return apply

    def build_library(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("library-page")
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        header.add_css_class("library-header")
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.library_title = label("Search", "page-title")
        self.library_meta = label("Find a song, artist, or paste a YouTube Music link", "page-subtitle")
        titles.append(self.library_title)
        titles.append(self.library_meta)
        header.append(titles)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search songs, artists, or paste a YouTube Music link")
        self.search.add_css_class("search-field")
        self.search.set_size_request(400, -1)
        self.search.set_halign(Gtk.Align.START)
        self.search.set_sensitive(False)
        self.search.connect("activate", self.search_tracks)
        header.append(self.search)
        page.append(header)
        self.track_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.track_list.add_css_class("tracks")
        self.track_list.connect("row-activated", self.row_activated)
        self.empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.empty_state.add_css_class("empty-state")
        self.empty_state.set_halign(Gtk.Align.CENTER)
        self.empty_state.set_valign(Gtk.Align.START)
        self.empty_state.set_margin_top(90)
        self.empty_icon = icon("system-search-symbolic", 30, "empty-icon")
        self.empty_title = label("Find something to play", "empty-title")
        self.empty_title.set_xalign(0.5)
        self.empty_detail = label("Search YouTube Music or open a playlist to see tracks here.", "empty-detail", wrap=True)
        self.empty_detail.set_xalign(0.5)
        self.empty_detail.set_justify(Gtk.Justification.CENTER)
        self.empty_detail.set_max_width_chars(46)
        self.empty_state.append(self.empty_icon)
        self.empty_state.append(self.empty_title)
        self.empty_state.append(self.empty_detail)
        self.library_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.library_body.append(self.track_list)
        self.library_body.append(self.empty_state)
        more_row = Gtk.Box()
        more_row.set_halign(Gtk.Align.CENTER)
        more_row.set_margin_top(14)
        more_row.set_margin_bottom(18)
        self.more = button("Load more", self.load_more, "text-button")
        self.more.set_visible(False)
        more_row.append(self.more)
        self.library_body.append(more_row)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("library-scroll")
        scroll.set_child(self.library_body)
        page.append(scroll)
        self.next_path = None
        self.show_empty("system-search-symbolic", "Find something to play", "Search YouTube Music or open a playlist to see tracks here.")
        return page

    def build_lyrics(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("lyrics-page")
        header = Gtk.Box(spacing=15)
        header.add_css_class("lyrics-header")
        self.lyrics_art = Artwork(64, 7, "music-note-symbolic")
        self.lyrics_art.add_css_class("lyrics-art")
        header.append(self.lyrics_art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True)
        self.song_title = label("Nothing playing", "lyrics-title")
        self.song_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.song_title.set_max_width_chars(44)
        self.artist = label("Choose a song from your library", "lyrics-artist")
        self.artist.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist.set_max_width_chars(44)
        self.sync_type = label("", "sync-badge")
        self.sync_type.set_visible(False)
        info.append(self.song_title)
        info.append(self.artist)
        info.append(self.sync_type)
        header.append(info)
        self.import_button = Gtk.Button()
        self.import_button.add_css_class("ghost-button")
        self.import_button.set_tooltip_text("Import a TTML or LRC file")
        import_content = Gtk.Box(spacing=8)
        import_content.append(icon("document-open-symbolic", 15))
        import_content.append(label("Import lyrics"))
        self.import_button.set_child(import_content)
        self.import_button.set_valign(Gtk.Align.CENTER)
        self.import_button.connect("clicked", lambda *_: self.choose_lyrics())
        header.append(self.import_button)
        header.append(button("Match lyrics", self.match_lyrics, "ghost-button"))
        page.append(header)
        self.lyric_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.lyric_scroll.add_css_class("lyrics-scroll")
        self.lyric_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.lyric_box.add_css_class("lyrics")
        self.lyric_box.set_margin_start(38)
        self.lyric_box.set_margin_end(38)
        self.lyric_box.set_margin_top(52)
        self.lyric_box.set_margin_bottom(120)
        self.lyric_scroll.set_child(self.lyric_box)
        lyric_overlay = Gtk.Overlay()
        lyric_overlay.set_vexpand(True)
        lyric_overlay.set_child(self.lyric_scroll)
        fade = Gtk.Box()
        fade.add_css_class("lyrics-fade-top")
        fade.set_valign(Gtk.Align.START)
        fade.set_size_request(-1, 58)
        fade.set_can_target(False)
        lyric_overlay.add_overlay(fade)
        page.append(lyric_overlay)
        footer = Gtk.Box()
        footer.add_css_class("lyrics-footer")
        self.credits = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.credits.add_css_class("credits")
        self.credits.set_max_width_chars(120)
        self.credits.connect("activate-link", self.open_credit)
        footer.append(self.credits)
        page.append(footer)
        return page

    def status(self, text):
        self.message.set_text(text)
        self.message.set_tooltip_text(text)

    def build_player(self):
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        bar.add_css_class("player")
        layout = Gtk.CenterBox()
        layout.add_css_class("player-content")
        meta = Gtk.Button()
        meta.add_css_class("player-meta")
        meta.set_tooltip_text("Show lyrics")
        meta.connect("clicked", lambda *_: self.show_page("lyrics"))
        meta_content = Gtk.Box(spacing=12)
        self.player_art = Artwork(56, 6, "music-note-symbolic")
        self.player_art.add_css_class("player-art")
        meta_content.append(self.player_art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.player_title = label("Nothing playing", "player-title")
        self.player_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.player_title.set_max_width_chars(24)
        self.player_artist = label("Choose a song from your library", "player-artist")
        self.player_artist.set_ellipsize(Pango.EllipsizeMode.END)
        self.player_artist.set_max_width_chars(24)
        info.append(self.player_title)
        info.append(self.player_artist)
        meta_content.append(info)
        meta.set_child(meta_content)
        meta.set_size_request(280, -1)
        layout.set_start_widget(meta)
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        transport = Gtk.Box(spacing=8)
        transport.set_halign(Gtk.Align.CENTER)
        self.shuffle_button = toggle_button("media-playlist-shuffle-symbolic", "Shuffle queue", self.toggle_shuffle)
        transport.append(self.shuffle_button)
        transport.append(icon_button("media-skip-backward-symbolic", "Previous track", lambda: self.skip(-1), 17))
        self.play_button = Gtk.Button()
        self.play_button.add_css_class("play-button")
        self.play_icon = icon("media-playback-start-symbolic", 16)
        self.play_button.set_child(self.play_icon)
        self.play_button.set_tooltip_text("Play (Space)")
        self.play_button.set_sensitive(False)
        self.play_button.connect("clicked", lambda *_: self.toggle_play())
        transport.append(self.play_button)
        transport.append(icon_button("media-skip-forward-symbolic", "Next track", lambda: self.skip(1), 17))
        self.repeat_button = toggle_button("media-playlist-repeat-symbolic", "Repeat queue", self.toggle_repeat)
        transport.append(self.repeat_button)
        self.like_button = icon_button("heart-symbolic", "Save to Liked Songs", self.toggle_like)
        self.like_button.set_sensitive(False)
        transport.append(self.like_button)
        center.append(transport)
        progress = Gtk.Box(spacing=9)
        progress.set_halign(Gtk.Align.CENTER)
        progress.set_size_request(420, -1)
        self.elapsed = label("0:00", "timecode")
        self.elapsed.set_xalign(1)
        self.elapsed.set_width_chars(4)
        self.total = label("0:00", "timecode")
        self.total.set_width_chars(4)
        self.seek = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.1)
        self.seek.set_draw_value(False)
        self.seek.set_hexpand(True)
        self.seek.set_tooltip_text("Playback position")
        self.seek.connect("change-value", self.seek_changed)
        progress.append(self.elapsed)
        progress.append(self.seek)
        progress.append(self.total)
        center.append(progress)
        layout.set_center_widget(center)
        right = Gtk.Box(spacing=8)
        right.set_halign(Gtk.Align.END)
        right.set_valign(Gtk.Align.CENTER)
        right.set_size_request(184, -1)
        self.volume_icon = icon("audio-volume-high-symbolic", 15, "player-icon")
        right.append(self.volume_icon)
        volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        volume.add_css_class("volume")
        volume.set_draw_value(False)
        volume.set_size_request(92, -1)
        volume.set_value(50)
        volume.set_tooltip_text("Volume")
        volume.connect("value-changed", self.volume_changed)
        right.append(volume)
        layout.set_end_widget(right)
        bar.append(layout)
        return bar

    def volume_changed(self, scale):
        value = scale.get_value()
        name = "audio-volume-muted-symbolic" if value == 0 else "audio-volume-low-symbolic" if value < 34 else "audio-volume-medium-symbolic" if value < 67 else "audio-volume-high-symbolic"
        self.volume_icon.set_from_icon_name(name)
        self.send("volume", value=round(value * 655.35))

    def settings(self):
        dialog = Gtk.Window(title="Settings", transient_for=self.window, modal=True, default_width=520)
        header = Gtk.HeaderBar()
        header.set_title_widget(label("Settings", "dialog-title"))
        header.set_show_title_buttons(True)
        dialog.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.add_css_class("settings")
        box.append(label("YouTube Music", "settings-heading"))
        box.append(label("Search songs or paste public playlist links. Audio plays inside Ember. This unofficial integration can break; restricted tracks may require sign-in and account login is not implemented.", "settings-copy", wrap=True))
        box.append(label("Transfer Spotify playlists", "settings-heading"))
        box.append(label("Use a transfer service to move playlists to YouTube Music, review the matches, make the playlist public or unlisted, then paste its link in Search.", wrap=True))
        box.append(Gtk.LinkButton(uri="https://www.tunemymusic.com/", label="Transfer with TuneMyMusic"))
        box.append(Gtk.LinkButton(uri="https://soundiiz.com/", label="Transfer with Soundiiz"))
        box.append(label("Lyrics", "settings-heading"))
        box.append(label("Spicy Lyrics key, optional. Local TTML and LRC imports work without it.", "settings-copy", wrap=True))
        key = Gtk.PasswordEntry(show_peek_icon=True)
        key.add_css_class("field")
        key.set_text(self.spotify.settings.get("lyrics_key", ""))
        box.append(key)
        lyrics_link = Gtk.LinkButton(uri="https://developers.spicylyrics.org/dashboard/applications", label="Create a Spicy Lyrics key")
        lyrics_link.add_css_class("link-button")
        lyrics_link.set_halign(Gtk.Align.START)
        box.append(lyrics_link)
        box.append(label("For a native key, enable “No origin header”. Keys and session tokens remain in private local files outside this project.", "field-note", wrap=True))
        feedback = label("", "settings-feedback", wrap=True)
        box.append(feedback)
        actions = Gtk.Box(spacing=8)
        actions.set_halign(Gtk.Align.END)
        actions.set_margin_top(4)
        def save():
            if self.login_pending:
                feedback.set_text("Finish sign-in before changing credentials.")
                return
            self.spotify.settings["lyrics_key"] = key.get_text().strip()
            try:
                save_private(DATA / "settings.json", self.spotify.settings)
            except OSError:
                feedback.set_text("Could not save settings. Check file permissions.")
                return
            dialog.destroy()
            self.status("Settings saved. YouTube Music playback needs no Spotify sign-in.")
        actions.append(button("Close", dialog.destroy, "ghost-button"))
        actions.append(button("Save settings", save, "accent"))
        box.append(actions)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(box)
        dialog.set_default_size(520, 650)
        dialog.set_child(scroll)
        dialog.present()

    def forget_library(self):
        clear(self.track_list)
        clear(self.playlists)
        clear(self.home_playlists)
        self.visible_tracks = []
        self.queue = []
        self.queue_index = -1
        self.next_path = None
        self.more.set_visible(False)
        self.playlist_count = 0
        self.playlist_rows = {}
        self.active_playlist = None
        self.playlist_hint.set_text("Paste a public YouTube Music playlist link in Search.")
        self.playlist_hint.set_visible(True)
        self.home_shelf.set_visible(False)
        self.home_empty.set_visible(False)
        self.library_source = "search"
        self.library_title.set_text("Search")
        self.library_meta.set_text("Find a song, artist, or paste a YouTube Music link")
        self.show_empty("system-search-symbolic", "Find something to play", "Search YouTube Music or open a playlist to see tracks here.")
        self.set_active(None)

    def set_account_state(self, name):
        self.account_name = name
        self.home_guest.set_visible(True)
        self.guest_shelf.set_visible(False)
        self.home_user.set_visible(True)
        self.search.set_sensitive(True)
        if name:
            self.account_label.set_text(name)
            self.account_button.add_css_class("connected")
            self.account_button.set_tooltip_text("Search YouTube Music — no account setup needed")
        else:
            self.account_label.set_text("YouTube Music")
            self.account_button.remove_css_class("connected")
            self.account_button.set_tooltip_text("Search YouTube Music")

    def set_metadata(self, title, subtitle):
        self.song_title.set_text(title)
        self.artist.set_text(subtitle)
        self.player_title.set_text(title)
        self.player_artist.set_text(subtitle)

    def reset_player(self):
        self.demo = False
        self.track = None
        self.playing = False
        self.position = 0
        self.anchor = time.monotonic()
        self.duration = 0
        self.current_art_url = None
        self.seek.set_range(0, 1)
        self.seek.set_value(0)
        self.elapsed.set_text("0:00")
        self.total.set_text("0:00")
        self.player_art.clear_art()
        self.lyrics_art.clear_art()
        self.set_metadata("Nothing playing", "Choose a song from your library")
        self.play_icon.set_from_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play (Space)")
        self.play_button.set_sensitive(False)
        self.sync_type.set_text("")
        self.sync_type.set_visible(False)
        self.render_lyrics([], {"source": "idle"})

    def audio_event(self, kind):
        if self.closed or self.demo or self.pending_track:
            return
        if kind == "playing":
            self.playing = True
            self.status("Playing YouTube Music directly")
        elif kind in ("paused", "buffering", "error", "missing-plugin", "ended", "loading"):
            self.position = self.current_position()
            self.playing = False
        self.anchor = time.monotonic()
        if kind == "missing-plugin":
            self.status("Audio codec missing. Install GStreamer Good plugins (gst-plugins-good on Arch), then restart Ember.")
        elif kind == "error":
            self.pending_track = False
            self.status("YouTube Music audio failed. Retry the track or choose another unrestricted stream.")
        elif kind == "ended":
            self.skip(1, ended=True)
        elif kind == "loading":
            self.status("Buffering YouTube Music audio…")

    def send(self, command, **data):
        if command == "seek" and not self.audio.command(command, **data):
            self.status("This YouTube Music stream does not support seeking yet.")
        elif command != "seek":
            self.audio.command(command, **data)

    def stop_engine(self):
        self.audio.command("stop")
        self.playing = False

    def load_playlists(self, *_, **__):
        self.open_search()
        self.status("Paste a public or unlisted YouTube Music playlist link into Search and press Enter.")

    def load_tracks(self, path, title, params=None, append=False, nav=None):
        self.pages.set_visible_child_name("library")
        self.library_request += 1
        generation = self.library_request
        self.library_title.set_text(title)
        self.library_meta.set_text("Loading…")
        if not append:
            self.library_source = "playlist" if isinstance(nav, Gtk.Button) else (nav or "search")
            self.last_nav = nav
            self.visible_tracks = []
            self.next_path = None
            self.more.set_visible(False)
            clear(self.track_list)
            self.set_active(nav if isinstance(nav, str) else None, nav if isinstance(nav, Gtk.Button) else None)
            self.show_empty("view-paged-symbolic", "Loading tracks…", "Fetching from YouTube Music.")
        def done(data):
            if generation != self.library_request:
                return
            data = data.get("tracks", data)
            tracks = [item.get("track", item.get("item", item)) for item in data.get("items", []) if item]
            tracks = [track for track in tracks if track and track.get("type") == "track" and track.get("uri") and not track.get("is_local")]
            if not append:
                self.visible_tracks = []
                clear(self.track_list)
            self.visible_tracks.extend(tracks)
            for track in tracks:
                self.track_list.append(self.track_row(track))
            self.next_path = data.get("next")
            self.more.set_visible(bool(self.next_path))
            count = len(self.visible_tracks)
            self.library_meta.set_text("1 track" if count == 1 else f"{count} tracks")
            if count:
                self.hide_empty()
            else:
                searching = title == "Search results"
                self.show_empty("system-search-symbolic" if searching else "view-paged-symbolic", "No songs found" if searching else "Nothing here to play", "Try another search, or paste a YouTube Music track or playlist link." if searching else "No YouTube Music songs were found.")
            self.status(f"{count} tracks loaded." if count else "No tracks found.")
        def failed(message):
            if generation != self.library_request:
                return
            self.library_meta.set_text("Couldn't load tracks")
            if not append:
                self.show_empty("dialog-warning-symbolic", "Couldn't load this list", message)
            self.status(message)
        def fetch():
            return {"items": self.saved_tracks.copy() if path == "me/tracks" else music_search((params or {}).get("q", path))}
        self.task(fetch, done, failed)

    def track_row(self, track):
        row = Gtk.ListBoxRow()
        row.add_css_class("track-row")
        body = Gtk.Box(spacing=14)
        art = Artwork(40, 5, "music-note-symbolic")
        art.add_css_class("row-art")
        body.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
        name = label(track.get("name", "Untitled"), "row-title")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(54)
        artists = " · ".join(artist.get("name", "") for artist in track.get("artists") or [])
        artist = label(artists or "Unknown artist", "row-artist")
        artist.set_ellipsize(Pango.EllipsizeMode.END)
        artist.set_max_width_chars(56)
        info.append(name)
        info.append(artist)
        body.append(info)
        duration = label(timecode((track.get("duration_ms") or 0) / 1000), "row-duration")
        body.append(duration)
        row.set_child(body)
        row.set_tooltip_text(name.get_text() + " · " + artist.get_text())
        images = (track.get("album") or {}).get("images") or []
        self.load_artwork(best_image(images, 64), self.art_callback(art))
        return row

    def load_more(self):
        if self.next_path and self.next_path.startswith("https://api.spotify.com/v1/"):
            self.load_tracks(self.next_path.split("/v1/", 1)[1], self.library_title.get_text(), append=True, nav=self.last_nav)

    def search_tracks(self, *_):
        query = self.search.get_text().strip()
        if query:
            self.load_tracks(query, "Search results", nav="search")

    def row_activated(self, _box, row):
        if not getattr(self, "visible_tracks", None):
            return
        self.queue = self.visible_tracks.copy()
        self.queue_index = row.get_index()
        self.play_track(self.queue[self.queue_index])

    def play_track(self, track):
        self.send("stop")
        self.demo = False
        self.pending_track = True
        self.playing = False
        self.track = track
        self.request_id += 1
        generation = self.request_id
        self.position = 0
        self.duration = max(1, (track.get("duration_ms") or 1) / 1000)
        self.set_metadata(track["name"], " · ".join(artist["name"] for artist in track["artists"]))
        self.play_button.set_sensitive(False)
        self.render_lyrics([], {"source": "loading"})
        self.status("Resolving a full YouTube Music stream…")
        def ready(result):
            if generation != self.request_id:
                return
            resolved, stream = result
            self.pending_track = False
            self.track = resolved
            self.duration = max(1, resolved["duration_ms"] / 1000)
            self.seek.set_range(0, self.duration)
            self.set_metadata(resolved["name"], " · ".join(artist["name"] for artist in resolved["artists"]))
            self.play_button.set_sensitive(True)
            self.audio.load(stream)
            self.load_track_lyrics(resolved, generation)
            self.update_like()
        def failed(message):
            if generation == self.request_id:
                self.pending_track = False
                self.render_lyrics([], {"source": "unavailable"})
                self.status(message)
        self.task(lambda: music_resolve(track), ready, failed)
        url = best_image((track.get("album") or {}).get("images") or [], 400)
        self.current_art_url = url
        self.player_art.clear_art()
        self.lyrics_art.clear_art()
        if url:
            def show(texture):
                if url == self.current_art_url:
                    self.player_art.set_texture(texture)
                    self.lyrics_art.set_texture(texture)
            self.load_artwork(url, show)

    def load_track_lyrics(self, track, generation):
        def done(result):
            if generation == self.request_id:
                self.render_lyrics(*result)
        def failed(_message):
            if generation == self.request_id:
                self.render_lyrics([], {"source": "unavailable"})
                self.credits.set_text("No exact lyrics match. Import a TTML/LRC file for this recording.")
                self.credits.set_visible(True)
        self.task(lambda: metadata_lyrics(track), done, failed)

    def match_lyrics(self):
        if not self.track or self.demo:
            self.status("Choose a YouTube Music track first.")
            return
        window = Gtk.Window(title="Match word-synced lyrics", transient_for=self.window, modal=True, default_width=480)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.add_css_class("settings")
        box.append(label("Paste the Spotify link for the exact same recording. This looks up lyrics only; it does not play Spotify audio or require Spotify Premium.", wrap=True))
        entry = Gtk.Entry(placeholder_text="https://open.spotify.com/track/…")
        box.append(entry)
        generation = self.request_id
        def match():
            import re
            value = entry.get_text().strip()
            parsed = urllib.parse.urlsplit(value)
            track_id = parsed.path.split("/track/", 1)[-1] if parsed.hostname == "open.spotify.com" else value.removeprefix("spotify:track:")
            if not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
                self.status("Enter a valid Spotify track link or 22-character track ID.")
                return
            def done(result):
                if generation == self.request_id:
                    self.request_id += 1
                    self.render_lyrics(*result)
            self.task(lambda: fetch_lyrics(track_id, self.spotify.settings.get("lyrics_key", "")), done)
            window.destroy()
        box.append(button("Load timed lyrics", match, "accent"))
        window.set_child(box)
        window.present()

    def update_like(self):
        self.like_button.set_sensitive(self.track is not None and not self.demo)
        liked = self.track and any(item["id"] == self.track["id"] for item in self.saved_tracks)
        self.like_button.set_tooltip_text("Remove from Liked Songs" if liked else "Save to Liked Songs")
        (self.like_button.add_css_class if liked else self.like_button.remove_css_class)("active")

    def toggle_like(self):
        if not self.track or self.demo:
            return
        tracks = [item for item in self.saved_tracks if item["id"] != self.track["id"]]
        if len(tracks) == len(self.saved_tracks):
            tracks.append(self.track)
        try:
            save_private(DATA / "youtube-library.json", {"tracks": tracks})
        except OSError:
            self.status("Could not save Liked Songs; check file permissions.")
            return
        self.saved_tracks = tracks
        self.update_like()

    def render_lyrics(self, lines, body):
        self.lines = lines
        self.active_line = -1
        self.active_lines = set()
        if hasattr(self, "scroll_target"):
            del self.scroll_target
        self.lyric_labels = []
        clear(self.lyric_box)
        if not lines:
            messages = {"idle": "Choose a song to see its lyrics.", "loading": "Looking for lyrics…", "unavailable": "No synced lyrics for this track."}
            empty = label(messages.get(body.get("source"), "Lyrics will appear here."), "lyric-empty", wrap=True)
            self.lyric_box.append(empty)
        for line in lines:
            widget = Gtk.Label(label=line.text, xalign=1 if line.opposite else 0)
            widget.add_css_class("lyric-line")
            widget.set_wrap(True)
            widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            widget.set_selectable(True)
            if line.background:
                widget.add_css_class("background-vocal")
            click = Gtk.GestureClick()
            click.connect("released", lambda _g, _n, _x, _y, start=line.start: self.set_position(start) if start >= 0 else None)
            widget.add_controller(click)
            self.lyric_box.append(widget)
            self.lyric_labels.append(widget)
        source = body.get("source", "unknown")
        providers = {"lrclib": "Lyrics · LRCLIB", "spicy_lyrics": "Spicy Lyrics", "apple_music": "Apple Music", "spotify": "Spotify", "local": "Imported local file", "preview": "Original Ember sample · silent preview", "idle": "", "loading": ""}
        credit = html.escape(providers.get(source, "Lyrics unavailable"))
        if source == "spicy_lyrics":
            attribution = body.get("UploadAttribution", {})
            for role, prefix in (("Uploader", "uploaded by"), ("Maker", "made by")):
                contributor = attribution.get(role)
                if contributor:
                    name = html.escape(contributor.get("username", "Contributor"))
                    url = contributor.get("url", "")
                    if urllib.parse.urlsplit(url).scheme == "https" and urllib.parse.urlsplit(url).hostname == "spicylyrics.org":
                        name = '<a href="' + html.escape(url, quote=True) + '">' + name + '</a>'
                    credit += " · " + prefix + " " + name
        if credit:
            self.credits.set_markup(credit)
            self.credits.set_visible(True)
        else:
            self.credits.set_markup("")
            self.credits.set_visible(False)
        if lines:
            kind = "WORD SYNC" if any(line.words for line in lines) else "LINE SYNC" if lines[0].start >= 0 else "PLAIN LYRICS"
            self.sync_type.set_text(kind + (" · SILENT PREVIEW" if self.demo else ""))
            self.sync_type.set_visible(True)
        else:
            self.sync_type.set_visible(False)
        self.lyric_scroll.get_vadjustment().set_value(0)
        if getattr(self, "mpris", None):
            self.mpris.changed()

    def open_credit(self, _label, url):
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname == "spicylyrics.org":
            Gio.AppInfo.launch_default_for_uri(url, None)
        return True

    def choose_lyrics(self):
        chooser = Gtk.FileChooserNative(title="Import TTML or LRC lyrics", transient_for=self.window, action=Gtk.FileChooserAction.OPEN)
        file_filter = Gtk.FileFilter()
        file_filter.set_name("Timed lyrics (.ttml, .xml, .lrc)")
        for pattern in ("*.ttml", "*.xml", "*.lrc"):
            file_filter.add_pattern(pattern)
        chooser.add_filter(file_filter)
        generation = self.request_id
        def response(dialog, result):
            if result == Gtk.ResponseType.ACCEPT:
                path = dialog.get_file().get_path()
                def done(lines):
                    if generation == self.request_id:
                        self.request_id += 1  # A pending network lookup must not replace the import.
                        self.render_lyrics(lines, {"source": "local"})
                self.task(lambda: import_lyrics(path), done)
            dialog.destroy()
        chooser.connect("response", response)
        chooser.show()

    def current_position(self):
        if self.track and not self.demo and not self.pending_track:
            actual = self.audio.position()
            if actual is not None:
                return actual
        return min(self.duration, self.position + (time.monotonic() - self.anchor if self.playing else 0))

    def set_position(self, value):
        value = max(0, min(self.duration, value))
        self.position = value
        self.anchor = time.monotonic()
        if not self.demo and self.track:
            self.send("seek", ms=round(value * 1000))
        if getattr(self, "mpris", None):
            self.mpris.seeked()

    def seek_changed(self, _scale, _scroll, value):
        self.set_position(value)
        return False

    def toggle_play(self):
        if not self.demo and not self.track:
            return
        if self.demo:
            self.position = self.current_position()
            if self.position >= self.duration:
                self.position = 0
            self.anchor = time.monotonic()
            self.playing = not self.playing
            self.status("Silent lyrics preview · original sample words")
        else:
            self.send("pause" if self.playing else "play")

    def start_demo(self):
        self.show_page("lyrics")
        self.send("stop")
        self.request_id += 1
        self.demo = True
        self.track = None
        self.position = 0
        self.anchor = time.monotonic()
        self.duration = 40
        self.playing = True
        self.seek.set_range(0, 40)
        self.seek.set_value(0)
        self.set_metadata("A little room to listen", "Silent lyrics preview")
        self.play_button.set_sensitive(True)
        self.current_art_url = None
        self.player_art.clear_art()
        self.lyrics_art.clear_art()
        self.render_lyrics(demo_lines(), {"source": "preview"})
        self.status("Silent lyrics preview · original sample words")

    def toggle_shuffle(self):
        self.shuffle = self.shuffle_button.get_active()
        self.shuffle_button.set_tooltip_text("Shuffle on" if self.shuffle else "Shuffle queue")

    def toggle_repeat(self):
        self.repeat = self.repeat_button.get_active()
        self.repeat_button.set_tooltip_text("Repeat on" if self.repeat else "Repeat queue")

    def skip(self, direction, ended=False):
        if self.demo:
            self.set_position(self.current_position() + direction * 5)
            return
        if not self.queue:
            return
        index = self.queue_index + direction
        if self.shuffle and len(self.queue) > 1 and direction > 0:
            index = random.choice([i for i in range(len(self.queue)) if i != self.queue_index])
        if index >= len(self.queue) and not self.repeat:
            if ended:
                self.playing = False
                self.status("End of queue")
            return
        self.queue_index = index % len(self.queue)
        self.play_track(self.queue[self.queue_index])

    def tick(self):
        if self.closed:
            return False
        if not self.window.get_mapped() or self.window.is_suspended():
            return True
        position = self.current_position()
        if self.demo and self.playing and position >= self.duration:
            self.playing = False
            self.position = self.duration
        self.seek.set_value(position)
        self.elapsed.set_text(timecode(position))
        self.total.set_text(timecode(self.duration))
        icon_name = "media-playback-pause-symbolic" if self.playing else "media-playback-start-symbolic"
        if getattr(self, "play_icon_name", None) != icon_name:
            self.play_icon_name = icon_name
            self.play_icon.set_from_icon_name(icon_name)
            self.play_button.set_tooltip_text(("Pause" if self.playing else "Play") + " (Space)")
        if getattr(self, "last_playing", None) != self.playing:
            self.last_playing = self.playing
            if getattr(self, "mpris", None):
                self.mpris.changed()
        if self.pages.get_visible_child_name() != "lyrics":
            return True
        active_lines = {i for i, line in enumerate(self.lines) if 0 <= line.start <= position < line.end}
        active = next((i for i in sorted(active_lines) if not self.lines[i].background), min(active_lines, default=-1))
        changed = active != self.active_line
        if changed:
            self.active_line = active
        for index in self.active_lines - active_lines:
            widget = self.lyric_labels[index]
            widget.remove_css_class("active-line")
            widget.set_text(self.lines[index].text)
        self.active_lines = active_lines
        for index in active_lines:
            line, widget = self.lines[index], self.lyric_labels[index]
            widget.add_css_class("active-line")
            if line.words:
                def shade(word):
                    progress = max(0, min(1, (position - word.start) / max(0.01, word.end - word.start)))
                    return "#" + "".join(f"{round(a + (b-a)*progress):02x}" for a, b in zip((118, 118, 124), (255, 255, 255)))
                markup = "".join('<span foreground="' + shade(word) + '">' + html.escape(word.text) + '</span>' for word in line.words)
                widget.set_markup(markup)
            if changed and index == active:
                target = widget.get_allocation().y - self.lyric_scroll.get_height() * 0.38
                self.scroll_target = max(0, target)
        if hasattr(self, "scroll_target"):
            adjustment = self.lyric_scroll.get_vadjustment()
            target = min(self.scroll_target, max(0, adjustment.get_upper() - adjustment.get_page_size()))
            delta = target - adjustment.get_value()
            adjustment.set_value(adjustment.get_value() + delta * (0.16 if self.animations else 1))
            if abs(delta) < 1:
                del self.scroll_target
        return True

    def key_pressed(self, _controller, key, _code, state):
        focus = self.window.get_focus()
        if key == Gdk.KEY_f and state & Gdk.ModifierType.CONTROL_MASK:
            self.open_search()
            return True
        if key in (Gdk.KEY_XF86AudioNext, Gdk.KEY_XF86AudioPrev):
            self.skip(1 if key == Gdk.KEY_XF86AudioNext else -1)
            return True
        if key in (Gdk.KEY_XF86AudioPlay, Gdk.KEY_XF86AudioPause):
            self.toggle_play()
            return True
        if isinstance(focus, (Gtk.Text, Gtk.Entry, Gtk.SearchEntry, Gtk.PasswordEntry)):
            return False
        if key == Gdk.KEY_space:
            if isinstance(focus, (Gtk.Button, Gtk.ToggleButton, Gtk.ListBoxRow)):
                return False
            self.toggle_play()
            return True
        return False

    def close(self, *_):
        self.closed = True
        self.stop_engine()
        self.audio.close()
        if getattr(self, "mpris", None):
            self.mpris.close()
        return False


if __name__ == "__main__":
    Ember().run()
