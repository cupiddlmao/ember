#!/usr/bin/env python3
"""Ember: a native GTK player with a separate, owned audio engine."""
import html
import json
import math
import queue
import re
from pathlib import Path
import random
import subprocess
import threading
import time
import urllib.parse

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Graphene, Gsk, Gtk, Pango

from artwork import artwork_url, download_artwork
from lyrics import demo_lines, metadata_lyrics
from music import search as music_search, resolve as music_resolve, library_playlists as account_library, cached_playlist, cached_library
from audio import Audio
from audio_cache import STORE as audio_store
from generations import RequestGenerations
import youtube_auth
import disk_cache
from playlists import add as add_saved_playlist, load as load_saved_playlists, remove as remove_saved_playlist, save as save_saved_playlists, clean as clean_playlist
from services import Spotify, DATA, load_private, save_private
from mpris import Mpris

ROOT = Path(__file__).resolve().parent
PLAYLISTS_FILE = DATA / "youtube-playlists.json"
AUDIO_PREFS_FILE = DATA / "audio-settings.json"


def audio_preferences():
    try:
        data = load_private(AUDIO_PREFS_FILE)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def cache_limit_gb(value):
    try:
        return max(1, min(20, int(value)))
    except (TypeError, ValueError):
        return 2


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
    widget.set_focus_on_click(False)
    widget.set_child(icon(name, size))
    widget.set_tooltip_text(tooltip)
    widget.add_css_class("icon-button")
    if css:
        widget.add_css_class(css)
    widget.connect("clicked", lambda *_: callback())
    return widget


def toggle_button(name, tooltip, callback):
    widget = Gtk.ToggleButton()
    widget.set_focus_on_click(False)
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


def count_label(value, singular, plural):
    return f"{value} {singular if value == 1 else plural}"


def best_image(images, target=320):
    images = [image for image in images or []
              if isinstance(image, dict) and image.get("url") and artwork_url(image["url"])]
    if not images:
        return None
    return min(images, key=lambda image: abs(image.get("width", 0) - target)).get("url")


def display_artist(value):
    name = str(value or "").strip()
    if name.lower().endswith(" - topic"):
        name = name[:-8].rstrip()
    return name or "Unknown artist"


def track_artists(track):
    return " · ".join(display_artist(artist.get("name") if isinstance(artist, dict) else artist)
                      for artist in track.get("artists") or [])


def track_images(track):
    images = [image for image in ((track.get("album") or {}).get("images") or [])
              if isinstance(image, dict) and artwork_url(image.get("url"))]
    if images:
        return images
    video = str(track.get("id") or "")
    if re.fullmatch(r"[\w-]{11}", video, flags=re.ASCII):
        return [{"url": f"https://i.ytimg.com/vi/{video}/mqdefault.jpg"}]
    return []


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

    @property
    def target(self):
        return max(64, min(512, self.size * 2))

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
        self.saved_playlists = load_saved_playlists(PLAYLISTS_FILE)
        self.account_playlists = []
        self.account_loading = False
        self.audio = Audio(self.audio_event)
        self.pending_track = False
        self.playback_origin = None
        self.audio_jobs = {}
        self.audio_lock = threading.RLock()
        self.audio_sequence = 0
        self.audio_queue = queue.PriorityQueue()
        self.track_rows = {}
        preferences = audio_preferences()
        self.smart_cache = preferences.get("smart_cache", True) is not False
        self.audio_cache_limit_gb = cache_limit_gb(preferences.get("cache_limit_gb", 2))
        audio_store.limit = self.audio_cache_limit_gb * 1024 ** 3
        threading.Thread(target=self.audio_worker, daemon=True).start()
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
        self.requests = RequestGenerations()
        self.library_request = 0
        self.shuffle = False
        self.repeat = False
        self.closed = False
        self.account_generation = 0
        self.engine_generation = 0
        self.visible_tracks = []
        self.library_source = "search"
        self.active_playlist = None
        self.playlist_meta = None
        self.playlist_unavailable = 0
        self.playlist_rows = {}
        self.track_art_scope = object()
        self.saved_art_scope = object()
        self.current_art_url = None
        self.animations = True
        self.lyric_frame_id = None
        self.scroll_frame_time = None
        self.lyric_word_time = 0
        self.art_cache = {}
        self.art_waiters = {}
        self.art_queued = set()
        self.art_lock = threading.RLock()
        self.art_sequence = 0
        self.art_queue = queue.PriorityQueue()
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
        audio_store.set_limit(self.audio_cache_limit_gb * 1024 ** 3)
        audio_store.cleanup()
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
        self.render_saved_playlists()
        if youtube_auth.auth_path().exists():
            GLib.idle_add(self.refresh_account_playlists)
        self.update_liked_summary()
        self.show_page("home")
        self.status("Search public YouTube Music or paste a public playlist link · no account needed")
        self.window.present()
        self.lyric_frame_id = self.window.add_tick_callback(self.lyric_frame_tick, None)
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
        self.set_active("search")
        self.playlist_meta = None
        self.playlist_panel.set_visible(False)
        self.library_title.set_text("Search")
        self.library_meta.set_text("Public YouTube Music · no account needed")
        self.clear_tracks()
        self.show_empty("system-search-symbolic", "Search public YouTube Music", "Results appear here. Paste a track or playlist link to open it directly.")
        self.status("Search public YouTube Music or paste a track or playlist link.")
        self.search.grab_focus()

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
        self.account_button.connect("clicked", lambda *_: self.google_login())
        account = Gtk.Box(spacing=7)
        account.append(icon("globe-symbolic", 15))
        self.account_label = label("YouTube Music", "account-label")
        self.account_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.account_label.set_max_width_chars(22)
        account.append(self.account_label)
        self.account_button.set_child(account)
        self.account_button.set_tooltip_text("Sign in with Google")
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
            ("downloads", "Downloads", "folder-download-symbolic", self.open_downloads),
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
        side.append(button("Refresh YouTube playlists", self.refresh_account_playlists, "text-button"))
        side.append(label("Playlists", "sidebar-eyebrow"))
        self.playlist_hint = label("No saved playlists yet. Paste a public playlist link in Search.", "sidebar-hint", wrap=True)
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
        footer.append(label("Public playback · no account", "footer-note"))
        footer.append(label("Saved locally + connected YouTube playlists", "footer-note"))
        side.append(footer)
        return side

    def build_home(self):
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.add_css_class("page-scroll")
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        body.add_css_class("home")
        body.append(label("Home", "page-title"))
        self.home_guest = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.home_guest.add_css_class("guest-panel")
        self.home_guest.append(label("YouTube Music, played here.", "hero-title", wrap=True))
        copy = label("Search public YouTube Music songs or paste a track or playlist link. Playback is native and needs no account. Save a playlist to keep it on this device.", "hero-copy", wrap=True)
        copy.set_max_width_chars(56)
        self.home_guest.append(copy)
        actions = Gtk.Box(spacing=10)
        actions.set_margin_top(4)
        actions.append(button("Search YouTube Music", self.open_search, "accent"))
        actions.append(button("Silent lyrics preview", self.start_demo, "ghost-button"))
        self.home_guest.append(actions)
        body.append(self.home_guest)
        self.home_user = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.home_user.add_css_class("home-user")
        self.home_user.append(self.liked_banner())
        heading = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        heading.append(label("Your playlists", "shelf-heading"))
        heading.append(label("Local saves and connected YouTube library", "shelf-sub"))
        self.home_user.append(heading)
        self.home_playlists = Gtk.Box(spacing=18)
        self.home_playlists.add_css_class("shelf")
        self.home_playlists.set_halign(Gtk.Align.START)
        self.home_shelf = Gtk.ScrolledWindow(vscrollbar_policy=Gtk.PolicyType.NEVER, hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, min_content_height=216)
        self.home_shelf.add_css_class("shelf-scroller")
        self.home_shelf.set_child(self.home_playlists)
        self.home_shelf.set_visible(False)
        self.home_user.append(self.home_shelf)
        self.home_empty = label("No saved playlists yet. Open a public playlist link in Search, then choose Save playlist to keep it here.", "empty-inline", wrap=True)
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
        self.liked_sub = label("Saved on this device", "banner-sub")
        text.append(self.liked_sub)
        content.append(text)
        content.append(icon("go-next-symbolic", 14, "chevron"))
        banner.set_child(content)
        banner.connect("clicked", lambda *_: self.open_liked())
        return banner

    def shelf_card(self, title, subtitle, callback, images=None, scope=None):
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
        self.load_cover(art, images, scope)
        return card

    def playlist_row(self, item):
        row = Gtk.Button()
        row.add_css_class("playlist-row")
        title = item.get("title") or item.get("name") or "Saved playlist"
        row.set_tooltip_text(title)
        content = Gtk.Box(spacing=9)
        art = Artwork(24, 4, "music-note-symbolic")
        art.add_css_class("row-art")
        content.append(art)
        name = label(title, "playlist-name")
        name.set_ellipsize(Pango.EllipsizeMode.END)
        name.set_max_width_chars(21)
        content.append(name)
        row.set_child(content)
        row.connect("clicked", lambda *_: self.open_playlist(item))
        self.playlist_rows[item["id"]] = row
        self.load_cover(art, item.get("images") or [], self.saved_art_scope)
        return row

    def open_playlist(self, item):
        self.load_playlist(item)

    def open_liked(self):
        self.library_request += 1
        self.pages.set_visible_child_name("library")
        self.library_source = "liked"
        self.set_active("liked")
        self.playlist_meta = None
        self.playlist_panel.set_visible(False)
        self.library_title.set_text("Liked Songs")
        tracks = self.saved_tracks.copy()
        self.show_tracks(tracks, "Nothing saved yet", "Use the heart in the player to save tracks on this device.")
        self.library_meta.set_text(count_label(len(tracks), "saved track", "saved tracks") + " · on this device")
        self.status("Liked Songs are stored on this device.")

    def open_downloads(self):
        self.library_request += 1
        self.pages.set_visible_child_name("library")
        self.library_source = "downloads"
        self.set_active("downloads")
        self.playlist_meta = None
        self.playlist_panel.set_visible(False)
        self.library_title.set_text("Downloads")
        try:
            tracks = audio_store.downloaded_tracks()
        except Exception:
            tracks = []
        self.show_tracks(tracks, "No offline downloads yet",
                         "Use the download button on a track to keep it on this device for offline listening.",
                         "folder-download-symbolic")
        self.library_meta.set_text(count_label(len(tracks), "downloaded track", "downloaded tracks") + " · available offline")
        self.status("Downloads are stored on this device.")

    def audio_worker(self):
        while True:
            _priority, _sequence, track, job = self.audio_queue.get()
            if self.closed:
                return
            try:
                entry = audio_store.download(track, pinned=job["pinned"], protect=(self.track or {}).get("id"))
                if job.get("pinned") and entry and not entry["pinned"]:
                    entry = audio_store.pin(entry["video_id"])
            except Exception as error:
                message = str(error) if isinstance(error, (ValueError, RuntimeError)) else "Download failed. Check your connection and try again."
                GLib.idle_add(self.audio_job_finished, job, None, message)
            else:
                GLib.idle_add(self.audio_job_finished, job, entry, None)

    def queue_audio_download(self, track, pinned=False, priority=2):
        if not track or not track.get("id"):
            return False
        video = track["id"]
        with self.audio_lock:
            job = self.audio_jobs.get(video)
            if job:
                if pinned and not job.get("pinned"):
                    job["pinned"] = True
                    self.status("Will keep this track offline when the download finishes.")
                    self.update_download_rows(video)
                return False
            job = {"track": track, "pinned": bool(pinned), "priority": priority}
            self.audio_jobs[video] = job
            self.audio_sequence += 1
            sequence = self.audio_sequence
        self.audio_queue.put((priority, sequence, track, job))
        if pinned:
            self.status("Downloading " + track.get("name", "track") + " for offline…")
        self.update_download_rows(video)
        return True

    def audio_job_finished(self, job, entry, message):
        if self.closed:
            return False
        video = (job.get("track") or {}).get("id")
        with self.audio_lock:
            self.audio_jobs.pop(video, None)
        if job.get("pinned") and entry and not entry["pinned"]:
            try:
                entry = audio_store.pin(entry["video_id"])
            except (OSError, RuntimeError) as error:
                message = message or str(error)
                entry = None
        self.update_download_rows(video)
        if job.get("pinned"):
            name = ((entry or {}).get("track") or job.get("track") or {}).get("name", "track")
            if message:
                self.status("Could not save this track offline: " + message)
            elif entry:
                self.status("Saved for offline: " + name)
            else:
                self.status("The automatic cache was cleared before this track could be kept offline.")
            if self.library_source == "downloads" and self.pages.get_visible_child_name() == "library":
                GLib.idle_add(self.open_downloads)
        return False

    def toggle_audio_download(self, track):
        if not track or not track.get("id"):
            return
        video = track["id"]
        with self.audio_lock:
            job = self.audio_jobs.get(video)
            if job:
                if not job.get("pinned"):
                    job["pinned"] = True
                    self.status("Will keep this track offline when the download finishes.")
                    self.update_download_rows(video)
                return
        try:
            entry = audio_store.entry(video)
        except Exception:
            entry = None
        if entry and entry["pinned"]:
            try:
                audio_store.remove(video)
            except OSError:
                self.status("Could not remove the offline file. Check file permissions.")
                return
            self.status("Offline copy removed.")
            self.update_download_rows(video)
            self.update_download_button()
            if self.library_source == "downloads" and self.pages.get_visible_child_name() == "library":
                GLib.idle_add(self.open_downloads)
            return
        if entry:
            try:
                audio_store.pin(video)
            except (OSError, RuntimeError) as error:
                self.status("Could not keep this track offline: " + str(error))
                return
            self.status("Saved for offline: " + track.get("name", "track"))
            self.update_download_rows(video)
            self.update_download_button()
            if self.library_source == "downloads" and self.pages.get_visible_child_name() == "library":
                GLib.idle_add(self.open_downloads)
            return
        self.queue_audio_download(track, pinned=True, priority=0)

    def toggle_current_download(self):
        if self.track and not self.demo:
            self.toggle_audio_download(self.track)

    def prefetch_next(self):
        if not self.smart_cache or not self.queue or self.shuffle:
            return
        index = self.queue_index + 1
        if index >= len(self.queue):
            if not self.repeat:
                return
            index = 0
        next_track = self.queue[index]
        if not next_track or (self.track or {}).get("id") == next_track.get("id"):
            return
        try:
            if audio_store.entry(next_track.get("id") or ""):
                return
        except Exception:
            pass
        self.queue_audio_download(next_track, pinned=False, priority=2)

    def update_download_rows(self, video=None):
        if not hasattr(self, "track_rows"):
            return
        videos = [video] if video is not None else list(self.track_rows)
        try:
            entries = audio_store.entries(videos)
        except Exception:
            entries = {}
        for item in videos:
            rows = self.track_rows.get(item, [])
            if not rows:
                continue
            entry = entries.get(item)
            with self.audio_lock:
                job = self.audio_jobs.get(item)
            for row in rows:
                button = getattr(row, "download_button", None)
                icon = getattr(row, "download_icon", None)
                if button is None or icon is None:
                    continue
                if job:
                    icon.set_from_icon_name("process-working-symbolic")
                    button.set_tooltip_text("Downloading for offline…" if job.get("pinned") else "Caching automatically · click to keep offline")
                    button.set_sensitive(not job.get("pinned"))
                    button.remove_css_class("active")
                elif entry and entry["pinned"]:
                    icon.set_from_icon_name("object-select-symbolic")
                    button.set_tooltip_text("Available offline · click to remove")
                    button.set_sensitive(True)
                    button.add_css_class("active")
                elif entry:
                    icon.set_from_icon_name("folder-download-symbolic")
                    button.set_tooltip_text("Cached automatically · click to keep offline")
                    button.set_sensitive(True)
                    button.remove_css_class("active")
                else:
                    icon.set_from_icon_name("folder-download-symbolic")
                    button.set_tooltip_text("Save for offline")
                    button.set_sensitive(True)
                    button.remove_css_class("active")
        self.update_download_button()

    def update_download_button(self):
        if not hasattr(self, "download_button"):
            return
        track = self.track
        if not track or self.demo:
            self.download_icon.set_from_icon_name("folder-download-symbolic")
            self.download_button.set_tooltip_text("Save current track for offline")
            self.download_button.set_sensitive(False)
            self.download_button.remove_css_class("active")
            return
        video = track.get("id")
        try:
            entry = audio_store.entry(video)
        except Exception:
            entry = None
        with self.audio_lock:
            job = self.audio_jobs.get(video)
        self.download_button.set_sensitive(not job or not job.get("pinned"))
        if job:
            self.download_icon.set_from_icon_name("process-working-symbolic")
            self.download_button.set_tooltip_text("Downloading for offline…" if job.get("pinned") else "Caching automatically · click to keep offline")
            self.download_button.remove_css_class("active")
        elif entry and entry["pinned"]:
            self.download_icon.set_from_icon_name("object-select-symbolic")
            self.download_button.set_tooltip_text("Available offline · click to remove")
            self.download_button.add_css_class("active")
        elif entry:
            self.download_icon.set_from_icon_name("folder-download-symbolic")
            self.download_button.set_tooltip_text("Cached automatically · click to keep offline")
            self.download_button.remove_css_class("active")
        else:
            self.download_icon.set_from_icon_name("folder-download-symbolic")
            self.download_button.set_tooltip_text("Save current track for offline")
            self.download_button.remove_css_class("active")

    def art_worker(self):
        while True:
            _priority, _sequence, key = self.art_queue.get()
            with self.art_lock:
                if not self.art_waiters.get(key):
                    self.art_queued.discard(key)
                    continue
            url, size = key
            epoch = disk_cache.generation()
            cache_key = f'{url}:{size}'
            data = disk_cache.get('artwork', cache_key)
            try:
                if data is None:
                    data = download_artwork(url)
                    stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
                    pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, size, size, True, None)
                    ok, resized = pixbuf.save_to_bufferv('png', [], [])
                    if ok:
                        data = bytes(resized)
                        disk_cache.put('artwork', cache_key, data, epoch)
            except Exception:
                data = None
            GLib.idle_add(self.art_delivered, key, data, epoch)

    def decode_artwork(self, data, size):
        try:
            stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(data))
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(stream, size, size, True, None)
            return Gdk.Texture.new_for_pixbuf(pixbuf)
        except Exception:
            try:
                return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
            except Exception:
                return None

    def art_delivered(self, key, data, epoch=None):
        with self.art_lock:
            if self.closed:
                return False
            callbacks = self.art_waiters.pop(key, [])
            self.art_queued.discard(key)
            if not callbacks:
                return False
            if epoch is not None and epoch != disk_cache.generation():
                return False
            texture = self.decode_artwork(data, key[1]) if data else None
            self.art_cache[key] = texture
            while len(self.art_cache) > 80:
                del self.art_cache[next(iter(self.art_cache))]
            for callback, _scope in callbacks:
                if texture is not None:
                    callback(texture)
            return False

    def load_artwork(self, url, callback, size=320, scope=None):
        with self.art_lock:
            if artwork_url(url) is None:
                return
            key = (url, int(size))
            if key in self.art_cache:
                if self.art_cache[key] is not None:
                    callback(self.art_cache[key])
                return
            self.art_waiters.setdefault(key, []).append((callback, scope))
            if key not in self.art_queued:
                self.art_queued.add(key)
                self.art_sequence += 1
                self.art_queue.put((0 if scope is None else 1, self.art_sequence, key))

    def retire_art_scope(self, scope):
        with self.art_lock:
            if scope is None:
                return
            for key in list(self.art_waiters):
                remaining = [entry for entry in self.art_waiters[key] if entry[1] != scope]
                if remaining:
                    self.art_waiters[key] = remaining
                else:
                    del self.art_waiters[key]

    def load_cover(self, widget, images, scope=None):
        target = widget.target
        self.load_artwork(best_image(images, target), self.art_callback(widget), target, scope)

    def art_callback(self, widget):
        def apply(texture):
            if widget.get_root() is not None:
                widget.set_texture(texture)
        return apply

    def build_playlist_panel(self):
        panel = Gtk.Box(spacing=18)
        panel.add_css_class("playlist-panel")
        panel.set_visible(False)
        self.playlist_art = Artwork(112, 9, "music-note-symbolic")
        self.playlist_art.add_css_class("playlist-art")
        panel.append(self.playlist_art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7, hexpand=True, valign=Gtk.Align.CENTER)
        self.playlist_title = label("", "playlist-title")
        self.playlist_title.set_ellipsize(Pango.EllipsizeMode.END)
        self.playlist_title.set_max_width_chars(58)
        self.playlist_detail = label("", "playlist-detail", wrap=True)
        self.playlist_detail.set_max_width_chars(86)
        info.append(self.playlist_title)
        info.append(self.playlist_detail)
        actions = Gtk.Box(spacing=8)
        self.playlist_play = button("Play", self.play_playlist, "accent")
        self.playlist_shuffle = button("Shuffle", self.shuffle_playlist, "ghost-button")
        self.playlist_save = button("Save playlist", self.toggle_playlist_save, "ghost-button")
        self.playlist_play.set_sensitive(False)
        self.playlist_shuffle.set_sensitive(False)
        actions.append(self.playlist_play)
        actions.append(self.playlist_shuffle)
        actions.append(self.playlist_save)
        info.append(actions)
        panel.append(info)
        return panel

    def build_library(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.add_css_class("library-page")
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        header.add_css_class("library-header")
        search_row = Gtk.Box(spacing=0)
        self.search = Gtk.SearchEntry()
        self.search.set_placeholder_text("Search public YouTube Music or paste a track or playlist link")
        self.search.add_css_class("search-field")
        self.search.set_hexpand(True)
        self.search.set_size_request(440, -1)
        self.search.set_halign(Gtk.Align.FILL)
        self.search.connect("activate", self.search_tracks)
        search_row.append(self.search)
        self.search_spacer = Gtk.Box()
        self.search_spacer.set_size_request(180, -1)
        search_row.append(self.search_spacer)
        header.append(search_row)
        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        self.library_title = label("Search", "page-title")
        self.library_meta = label("Public YouTube Music · no account needed", "page-subtitle")
        titles.append(self.library_title)
        titles.append(self.library_meta)
        header.append(titles)
        page.append(header)
        self.playlist_panel = self.build_playlist_panel()
        page.append(self.playlist_panel)
        self.track_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.track_list.add_css_class("tracks")
        self.track_list.connect("row-activated", self.row_activated)
        self.empty_state = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.empty_state.add_css_class("empty-state")
        self.empty_state.set_halign(Gtk.Align.CENTER)
        self.empty_state.set_valign(Gtk.Align.START)
        self.empty_state.set_margin_top(70)
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
        self.library_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.library_scroll.add_css_class("library-scroll")
        self.library_scroll.set_child(self.library_body)
        page.append(self.library_scroll)
        self.show_empty("system-search-symbolic", "Find something to play", "Search YouTube Music or open a playlist to see tracks here.")
        return page

    def build_lyrics(self):
        page = Gtk.Overlay()
        page.add_css_class("lyrics-page")
        self.lyrics_backdrop = Gtk.Picture()
        self.lyrics_backdrop.add_css_class("lyrics-backdrop")
        self.lyrics_backdrop.set_content_fit(Gtk.ContentFit.COVER)
        self.lyrics_backdrop.set_can_shrink(True)
        self.lyrics_backdrop.set_hexpand(True)
        self.lyrics_backdrop.set_vexpand(True)
        self.lyrics_backdrop.set_opacity(0.18)
        page.set_child(self.lyrics_backdrop)
        scrim = Gtk.Box()
        scrim.add_css_class("lyrics-scrim")
        scrim.set_hexpand(True)
        scrim.set_vexpand(True)
        scrim.set_can_target(False)
        page.add_overlay(scrim)
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.add_css_class("lyrics-content")
        content.set_hexpand(True)
        content.set_vexpand(True)
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
        content.append(header)
        self.lyric_scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.lyric_scroll.add_css_class("lyrics-scroll")
        self.lyric_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.lyric_box.add_css_class("lyrics")
        self.lyric_box.set_halign(Gtk.Align.CENTER)
        self.lyric_box.set_margin_start(44)
        self.lyric_box.set_margin_end(44)
        self.lyric_box.set_margin_top(64)
        self.lyric_box.set_margin_bottom(150)
        self.lyric_scroll.set_child(self.lyric_box)
        lyric_overlay = Gtk.Overlay()
        lyric_overlay.set_vexpand(True)
        lyric_overlay.set_child(self.lyric_scroll)
        fade = Gtk.Box()
        fade.add_css_class("lyrics-fade-top")
        fade.set_valign(Gtk.Align.START)
        fade.set_size_request(-1, 72)
        fade.set_can_target(False)
        lyric_overlay.add_overlay(fade)
        fade_bottom = Gtk.Box()
        fade_bottom.add_css_class("lyrics-fade-bottom")
        fade_bottom.set_valign(Gtk.Align.END)
        fade_bottom.set_size_request(-1, 96)
        fade_bottom.set_can_target(False)
        lyric_overlay.add_overlay(fade_bottom)
        content.append(lyric_overlay)
        footer = Gtk.Box()
        footer.add_css_class("lyrics-footer")
        self.credits = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.credits.add_css_class("credits")
        self.credits.set_max_width_chars(120)
        self.credits.connect("activate-link", self.open_credit)
        footer.append(self.credits)
        content.append(footer)
        page.add_overlay(content)
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
        meta.set_size_request(250, -1)
        start = Gtk.Box(spacing=4)
        start.set_valign(Gtk.Align.CENTER)
        start.append(meta)
        self.like_button = icon_button("heart-symbolic", "Save to Liked Songs", self.toggle_like)
        self.like_button.set_sensitive(False)
        start.append(self.like_button)
        layout.set_start_widget(start)
        center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        transport = Gtk.Box(spacing=8)
        transport.set_halign(Gtk.Align.CENTER)
        self.shuffle_button = toggle_button("media-playlist-shuffle-symbolic", "Shuffle queue", self.toggle_shuffle)
        transport.append(self.shuffle_button)
        transport.append(icon_button("media-skip-backward-symbolic", "Previous track", lambda: self.skip(-1), 17))
        self.play_button = Gtk.Button()
        self.play_button.set_focus_on_click(False)
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
        self.download_button = icon_button("folder-download-symbolic", "Save current track for offline", self.toggle_current_download)
        self.download_icon = self.download_button.get_child()
        self.download_button.set_sensitive(False)
        right.append(self.download_button)
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

    def clear_cache(self):
        def work():
            audio_store.clear_auto()
            disk_cache.clear()
        def done(_result):
            with self.art_lock:
                self.art_cache.clear()
            self.status("Cache cleared. Offline downloads, likes, saved playlists and login are unchanged.")
        self.task(work, done)

    def settings(self):
        dialog = Gtk.Window(title="Settings", transient_for=self.window, modal=True, default_width=520)
        header = Gtk.HeaderBar()
        header.set_title_widget(label("Settings", "dialog-title"))
        header.set_show_title_buttons(True)
        dialog.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.add_css_class("settings")
        box.append(label("YouTube Music", "settings-heading"))
        box.append(label("Search songs or paste public playlist links. Audio plays inside Ember. Google sign-in loads playlists you own, including private playlists. Open other creators’ playlists by link. Audio playback remains public; restricted tracks may be unavailable.", "settings-copy", wrap=True))
        box.append(button("Sign in with Google", lambda: (dialog.destroy(), self.google_login()), "accent"))
        box.append(button("Refresh YouTube account playlists", self.refresh_account_playlists, "ghost-button"))
        box.append(label("To connect or renew login, run .venv/bin/python import_youtube_auth.py in the project folder. Paste credentials only into its hidden local prompt. Browser authentication is unofficial and can expire. Removing the local login file disconnects account access.", "settings-copy", wrap=True))
        box.append(label("Saved on this device", "settings-heading"))
        box.append(label("Liked Songs and saved playlists are local metadata. They are not synced to a Google account.", "settings-copy", wrap=True))
        box.append(label("Transfer Spotify playlists locally", "settings-heading"))
        box.append(label("Use the existing CSV matching and review helper. It helps you approve matches and create an unlisted destination only after review; see TRANSFER.md. Ember itself does not upload playlists.", "settings-copy", wrap=True))
        guide = Gtk.LinkButton(uri=(ROOT / "TRANSFER.md").as_uri(), label="Open local transfer guide")
        guide.add_css_class("link-button")
        guide.set_halign(Gtk.Align.START)
        box.append(guide)
        box.append(label("Audio cache", "settings-heading"))
        box.append(label("Downloads are kept on this device until you remove them. Automatic cache is bounded and least-recently-used; clearing it never removes your offline downloads. Artwork is up to 200 MiB; playlist metadata is up to 20 MiB and expires after 7 days.", "settings-copy", wrap=True))
        self.smart_cache_toggle = Gtk.CheckButton(label="Cache upcoming queue tracks automatically")
        self.smart_cache_toggle.set_active(self.smart_cache)
        self.smart_cache_toggle.set_tooltip_text("Download the next track in the background so it can play without a network")
        box.append(self.smart_cache_toggle)
        limit_row = Gtk.Box(spacing=8)
        limit_row.append(label("Automatic cache limit", "field-label"))
        self.cache_limit_spin = Gtk.SpinButton.new_with_range(1, 20, 1)
        self.cache_limit_spin.set_value(self.audio_cache_limit_gb)
        self.cache_limit_spin.set_tooltip_text("Gigabytes of automatic audio cache")
        limit_row.append(self.cache_limit_spin)
        limit_row.append(label("GiB", "field-note"))
        box.append(limit_row)
        box.append(button("Clear cache", self.clear_cache, "ghost-button"))
        feedback = label("", "settings-feedback", wrap=True)
        box.append(feedback)
        actions = Gtk.Box(spacing=8)
        actions.set_halign(Gtk.Align.END)
        actions.set_margin_top(4)
        def save():
            smart = self.smart_cache_toggle.get_active()
            limit = cache_limit_gb(self.cache_limit_spin.get_value())
            try:
                save_private(DATA / "settings.json", self.spotify.settings)
                save_private(AUDIO_PREFS_FILE, {"smart_cache": smart, "cache_limit_gb": limit})
            except OSError:
                feedback.set_text("Could not save settings. Check file permissions.")
                return
            self.smart_cache = smart
            self.audio_cache_limit_gb = limit
            audio_store.set_limit(limit * 1024 ** 3)
            dialog.destroy()
            self.status("Settings saved. Public YouTube Music playback needs no account.")
        actions.append(button("Close", dialog.destroy, "ghost-button"))
        actions.append(button("Save settings", save, "accent"))
        box.append(actions)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_child(box)
        dialog.set_default_size(520, 650)
        dialog.set_child(scroll)
        dialog.present()

    def set_metadata(self, title, subtitle):
        self.song_title.set_text(title)
        self.artist.set_text(subtitle)
        self.player_title.set_text(title)
        self.player_artist.set_text(subtitle)

    def reset_player(self):
        self.requests.start_track()
        self.demo = False
        self.pending_track = False
        self.playback_origin = None
        self.track = None
        self.queue = []
        self.queue_index = -1
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
        self.lyrics_backdrop.set_paintable(None)
        self.set_metadata("Nothing playing", "Choose a song from your library")
        self.play_icon.set_from_icon_name("media-playback-start-symbolic")
        self.play_button.set_tooltip_text("Play (Space)")
        self.play_button.set_sensitive(False)
        self.sync_type.set_text("")
        self.sync_type.set_visible(False)
        self.render_lyrics([], {"source": "idle"})
        self.update_playing_row()
        self.update_download_button()

    def audio_event(self, kind):
        if self.closed or self.demo or self.pending_track:
            return
        if kind == "playing":
            self.playing = True
            if self.playback_origin == "download":
                self.status("Playing downloaded audio")
            elif self.playback_origin == "cache":
                self.status("Playing cached audio")
            else:
                self.status("Playing YouTube Music directly")
        elif kind in ("paused", "buffering", "error", "missing-plugin", "ended", "loading"):
            self.position = self.current_position()
            self.playing = False
        self.anchor = time.monotonic()
        if kind == "missing-plugin":
            self.status("Audio codec missing. Install GStreamer Good plugins (gst-plugins-good on Arch), then restart Ember.")
        elif kind == "error":
            self.pending_track = False
            if self.playback_origin in ("download", "cache"):
                self.status("This offline audio file could not be played. Remove and re-download it, or choose another track.")
            else:
                self.status("YouTube Music audio failed. Retry the track or choose another unrestricted stream.")
        elif kind == "ended":
            self.skip(1, ended=True)
        elif kind == "loading":
            self.status("Opening offline audio…" if self.playback_origin in ("download", "cache") else "Buffering YouTube Music audio…")

    def send(self, command, **data):
        if command == "seek" and not self.audio.command(command, **data):
            self.status("This YouTube Music stream does not support seeking yet.")
        elif command != "seek":
            self.audio.command(command, **data)

    def stop_engine(self):
        self.audio.command("stop")
        self.playing = False

    def clear_tracks(self):
        self.retire_art_scope(self.track_art_scope)
        self.track_art_scope = object()
        clear(self.track_list)
        self.visible_tracks = []
        self.track_rows = {}

    def update_liked_summary(self):
        if hasattr(self, "liked_sub"):
            self.liked_sub.set_text(count_label(len(self.saved_tracks), "saved track", "saved tracks") + " · on this device")

    def google_login(self):
        if getattr(self, "google_login_window", None) is not None:
            self.google_login_window.present()
            return
        # Browser approval must not leave a minimized modal blocking the player.
        dialog = Gtk.Window(application=self, title="Sign in with Google", modal=False, default_width=540)
        self.google_login_window = dialog
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.add_css_class("settings")
        box.append(label("Google account playlists", "settings-heading"))
        box.append(label("One-time setup: enable YouTube Data API v3 in Google Cloud and create an OAuth client for TVs and Limited Input devices. Configure the consent screen and add yourself as a test user if required. Enter that client's credentials below. This is a device-code flow: approve in your browser, then Ember connects automatically.", wrap=True))
        box.append(Gtk.LinkButton(uri="https://console.cloud.google.com/apis/credentials", label="Open Google Cloud credentials"))
        try:
            config = load_private(youtube_auth.CLIENT_FILE)
        except (OSError, ValueError):
            config = {}
        client_id = Gtk.Entry(placeholder_text="OAuth client ID")
        client_id.set_text(config.get("client_id", ""))
        secret = Gtk.PasswordEntry(show_peek_icon=True)
        secret.set_text(config.get("client_secret", ""))
        box.append(label("Client ID"))
        box.append(client_id)
        box.append(label("Client secret"))
        box.append(secret)
        feedback = label("Credentials and refresh tokens stay in private files on this PC.", wrap=True)
        feedback.set_selectable(True)
        box.append(feedback)
        cancelled = threading.Event()
        self.google_login_cancel = cancelled
        def close_login(*_):
            cancelled.set()
            self.google_login_window = None
            return False
        dialog.connect("close-request", close_login)
        def failed(message):
            if not cancelled.is_set():
                start.set_sensitive(True)
                feedback.set_text(message)
        def signed_in(success):
            if cancelled.is_set():
                return
            if success:
                close_login()
                dialog.destroy()
                self.refresh_account_playlists()
        def ready(result):
            if cancelled.is_set():
                return
            provider, code = result
            feedback.set_text("Open the Google page and enter this code: " + code["user_code"] + "\nWaiting for approval… Close this window to cancel.")
            link = Gtk.LinkButton(uri=code["verification_url"], label="Continue in browser")
            box.append(link)
            try:
                Gio.AppInfo.launch_default_for_uri(code["verification_url"], None)
            except GLib.Error:
                pass
            self.task(lambda: youtube_auth.finish(provider, code, cancelled), signed_in, failed)
        def begin():
            start.set_sensitive(False)
            feedback.set_text("Requesting Google sign-in code…")
            identity, password = client_id.get_text().strip(), secret.get_text()
            self.task(lambda: youtube_auth.begin(identity, password), ready, failed)
        start = button("Sign in with Google", begin, "accent")
        box.append(start)
        dialog.set_child(box)
        dialog.present()

    def refresh_account_playlists(self):
        if self.account_loading:
            return False
        if not youtube_auth.auth_path().exists():
            self.google_login()
            return False
        cached = cached_library(youtube_auth.auth_path())
        if isinstance(cached, list):
            self.account_playlists = [value for item in cached if (value := clean_playlist(item))]
            self.render_saved_playlists()
        self.account_loading = True
        self.status("Refreshing cached YouTube playlists…" if cached else "Loading YouTube account playlists…")
        def done(items):
            self.account_loading = False
            self.account_playlists = [value for item in items if (value := clean_playlist(item))]
            self.render_saved_playlists()
            self.status(f"Loaded {len(self.account_playlists)} playlists you own on YouTube.")
        def failed(_message):
            self.account_loading = False
            self.status("Could not read YouTube library. Use Sign in with Google in Settings to reconnect.")
        self.task(lambda: account_library(youtube_auth.auth_path()), done, failed)
        return False

    def saved_subtitle(self, item):
        count = int(item.get("track_count") or 0)
        return count_label(count, "track", "tracks") if count else "Saved playlist"

    def is_saved(self, playlist_id):
        return any(item.get("id") == playlist_id for item in self.saved_playlists)

    def render_saved_playlists(self):
        self.retire_art_scope(self.saved_art_scope)
        self.saved_art_scope = object()
        clear(self.playlists)
        clear(self.home_playlists)
        self.playlist_rows = {}
        self.active_playlist = None
        combined = {item["id"]: item for item in self.account_playlists}
        combined.update({item["id"]: item for item in self.saved_playlists})
        for item in combined.values():
            self.playlists.append(self.playlist_row(item))
            card = self.shelf_card(item["title"], self.saved_subtitle(item), lambda _button=None, value=item: self.open_playlist(value), item.get("images") or [], self.saved_art_scope)
            self.home_playlists.append(card)
        has_playlists = bool(combined)
        self.playlist_hint.set_visible(not has_playlists)
        self.home_shelf.set_visible(has_playlists)
        self.home_empty.set_visible(not has_playlists)
        if self.pages.get_visible_child_name() == "library" and self.playlist_meta:
            self.set_active(None, self.playlist_rows.get(self.playlist_meta.get("id")))

    def show_tracks(self, tracks, empty_title, empty_detail, empty_icon="view-paged-symbolic"):
        self.clear_tracks()
        self.visible_tracks = list(tracks)
        for track in self.visible_tracks:
            row = self.track_row(track)
            self.track_list.append(row)
        self.update_playing_row()
        self.update_download_rows()
        if self.visible_tracks:
            self.hide_empty()
        else:
            self.show_empty(empty_icon, empty_title, empty_detail)
        if hasattr(self, "library_scroll"):
            self.library_scroll.get_vadjustment().set_value(0)
            GLib.idle_add(self.reset_library_scroll)

    def reset_library_scroll(self):
        if hasattr(self, "library_scroll"):
            self.library_scroll.get_vadjustment().set_value(0)
        return False

    def update_playing_row(self):
        if not hasattr(self, "track_list"):
            return
        current = (self.track or {}).get("id")
        index = 0
        while row := self.track_list.get_row_at_index(index):
            if current and getattr(row, "track_id", None) == current:
                row.add_css_class("playing-row")
            else:
                row.remove_css_class("playing-row")
            index += 1

    def show_playlist_panel(self, meta, count=None, unavailable=0, loading=False):
        meta = meta or {}
        self.playlist_panel.set_visible(True)
        self.playlist_title.set_text(meta.get("title") or "YouTube Music playlist")
        if loading:
            detail = "Loading public playlist…"
        else:
            parts = ["Saved on this device" if self.is_saved(meta.get("id")) else "Public playlist"]
            if meta.get("owner"):
                parts.append(meta["owner"])
            if count is not None:
                parts.append(count_label(count, "playable track", "playable tracks"))
            if unavailable:
                parts.append(count_label(unavailable, "unavailable entry omitted", "unavailable entries omitted"))
            detail = " · ".join(parts)
        self.playlist_detail.set_text(detail)
        self.playlist_art_url = best_image(meta.get("images") or [], self.playlist_art.target)
        self.playlist_art.clear_art()
        if self.playlist_art_url:
            url = self.playlist_art_url
            def apply(texture):
                if url == self.playlist_art_url:
                    self.playlist_art.set_texture(texture)
            self.load_artwork(url, apply, self.playlist_art.target)
        self.playlist_unavailable = int(unavailable or 0)
        self.update_playlist_actions()
        self.playlist_save.set_sensitive(bool(meta.get("id")) and not loading)
        self.playlist_play.set_sensitive(bool(self.visible_tracks))
        self.playlist_shuffle.set_sensitive(len(self.visible_tracks) > 1)

    def update_playlist_actions(self):
        enabled = bool(self.playlist_meta and self.playlist_meta.get("id"))
        saved = enabled and self.is_saved(self.playlist_meta["id"])
        self.playlist_save.set_label("Remove saved" if saved else "Save playlist")
        self.playlist_save.set_tooltip_text("Stop keeping this playlist on this device" if saved else "Keep this playlist on this device")
        self.playlist_save.set_sensitive(enabled and self.playlist_panel.get_visible())

    def load_playlist(self, item):
        uri = item.get("uri")
        if not uri:
            self.status("This saved playlist has no valid public link.")
            return
        self.library_request += 1
        generation = self.library_request
        self.pages.set_visible_child_name("library")
        self.library_source = "playlist"
        self.playlist_meta = clean_playlist(item)
        self.clear_tracks()
        self.set_active(None, self.playlist_rows.get(item.get("id")))
        self.library_title.set_text("Playlist")
        self.library_meta.set_text("Loading playlist…")
        self.search.set_text("")
        if self.playlist_meta:
            self.show_playlist_panel(self.playlist_meta, loading=True)
        else:
            self.playlist_panel.set_visible(True)
            self.playlist_title.set_text("Playlist")
            self.playlist_detail.set_text("Validating public playlist…")
            self.playlist_save.set_sensitive(False)
        self.show_empty("view-paged-symbolic", "Loading playlist…", "Fetching all available public tracks.")
        self.status("Loading public playlist…")
        auth = youtube_auth.auth_path() if any(p["id"] == item.get("id") for p in self.account_playlists) else None
        cached = cached_playlist(uri, auth)
        if isinstance(cached, dict):
            self.show_playlist_result(cached)
            self.library_meta.set_text("Cached playlist · refreshing…")
        def done(result):
            if generation != self.library_request:
                return
            if result.get("kind") != "playlist":
                failed("The link did not return a public playlist.")
                return
            self.show_playlist_result(result)
        def failed(message):
            if generation != self.library_request:
                return
            if cached:
                self.library_meta.set_text("Cached playlist · refresh failed")
                self.status("Showing cached tracks. " + message)
                return
            self.library_meta.set_text("Couldn't load playlist")
            if self.playlist_meta:
                self.playlist_detail.set_text("Couldn't load this playlist.")
                self.playlist_save.set_sensitive(False)
            self.show_empty("dialog-warning-symbolic", "Couldn't load this playlist", message)
            self.status(message)
        self.task(lambda: music_search(uri, auth=auth), done, failed)

    def show_playlist_result(self, result):
        tracks = result.get("tracks") or []
        meta = clean_playlist(result.get("playlist") or {})
        if not meta:
            self.library_meta.set_text("Couldn't load playlist")
            if self.playlist_meta:
                self.playlist_detail.set_text("Couldn't load this playlist.")
                self.playlist_save.set_sensitive(False)
            self.show_empty("dialog-warning-symbolic", "Couldn't load this playlist", "YouTube Music did not return playlist metadata.")
            return
        if self.playlist_meta and self.playlist_meta.get("id") == meta["id"]:
            meta["owner"] = meta.get("owner") or self.playlist_meta.get("owner", "")
            meta["images"] = meta.get("images") or self.playlist_meta.get("images", [])
        unavailable = max(0, int(result.get("unavailable") or 0))
        meta["track_count"] = len(tracks)
        self.playlist_meta = meta
        saved = self.is_saved(meta["id"])
        if saved:
            self.saved_playlists = add_saved_playlist(self.saved_playlists, meta)
            try:
                self.saved_playlists = save_saved_playlists(PLAYLISTS_FILE, self.saved_playlists)
            except OSError:
                self.status("Playlist opened, but its saved details could not be updated.")
            self.render_saved_playlists()
        self.library_title.set_text("Playlist")
        self.library_meta.set_text("Saved on this device" if saved else "Public playlist · save to keep it here")
        self.search.set_text("")
        self.show_tracks(tracks, "No playable tracks", "This playlist returned no playable public tracks. Unavailable entries may be omitted.", "view-paged-symbolic")
        self.show_playlist_panel(meta, len(tracks), unavailable)
        self.status(count_label(len(tracks), "playable track", "playable tracks") + (" · " + count_label(unavailable, "unavailable entry omitted", "unavailable entries omitted") if unavailable else "") + " loaded.")

    def load_query(self, query):
        self.library_request += 1
        generation = self.library_request
        self.pages.set_visible_child_name("library")
        self.library_source = "search"
        self.set_active("search")
        self.playlist_meta = None
        self.playlist_panel.set_visible(False)
        self.library_title.set_text("Search")
        self.library_meta.set_text("Loading…")
        self.clear_tracks()
        self.show_empty("view-paged-symbolic", "Loading…", "Fetching from YouTube Music.")
        self.status("Searching public YouTube Music…")
        cached = cached_playlist(query)
        if isinstance(cached, dict):
            self.show_playlist_result(cached)
            self.library_meta.set_text("Cached playlist · refreshing…")
        def done(result):
            if generation != self.library_request:
                return
            if result.get("kind") == "playlist":
                self.show_playlist_result(result)
                return
            tracks = result.get("tracks") or []
            if result.get("kind") == "track":
                self.library_title.set_text("Selected track")
                self.library_meta.set_text(count_label(len(tracks), "selected track", "selected tracks"))
                self.show_tracks(tracks, "No track returned", "YouTube Music did not return a playable track.", "view-paged-symbolic")
            else:
                self.library_title.set_text("Search")
                self.library_meta.set_text(count_label(len(tracks), "result", "results") + " · public YouTube Music")
                self.show_tracks(tracks, "No songs found", "Try another search, or paste a track or playlist link.", "system-search-symbolic")
            self.status(count_label(len(tracks), "track", "tracks") + " loaded.")
        def failed(message):
            if generation != self.library_request:
                return
            if cached:
                self.library_meta.set_text("Cached playlist · refresh failed")
                self.status("Showing cached tracks. " + message)
                return
            self.library_meta.set_text("Couldn't search")
            self.show_empty("dialog-warning-symbolic", "Couldn't search", message)
            self.status(message)
        self.task(lambda: music_search(query), done, failed)

    def play_playlist(self):
        if not self.visible_tracks:
            return
        self.queue = list(self.visible_tracks)
        self.queue_index = 0
        self.play_track(self.queue[0])

    def shuffle_playlist(self):
        if not self.visible_tracks:
            return
        self.shuffle_button.set_active(True)
        self.queue = list(self.visible_tracks)
        self.queue_index = random.randrange(len(self.queue))
        self.play_track(self.queue[self.queue_index])

    def toggle_playlist_save(self):
        meta = self.playlist_meta
        if not meta or not meta.get("id"):
            return
        saved = self.is_saved(meta["id"])
        records = remove_saved_playlist(self.saved_playlists, meta["id"]) if saved else add_saved_playlist(self.saved_playlists, meta)
        try:
            self.saved_playlists = save_saved_playlists(PLAYLISTS_FILE, records)
        except OSError:
            self.status("Could not update saved playlists; check file permissions.")
            return
        self.render_saved_playlists()
        self.show_playlist_panel(meta, len(self.visible_tracks), self.playlist_unavailable)
        self.library_meta.set_text("Saved on this device" if not saved else "Public playlist · save to keep it here")
        self.status("Playlist saved locally." if not saved else "Playlist removed from this device.")

    def track_row(self, track):
        row = Gtk.ListBoxRow()
        row.track_id = track.get("id")
        row.add_css_class("track-row")
        body = Gtk.Box(spacing=14)
        art = Artwork(40, 5, "music-note-symbolic")
        art.add_css_class("row-art")
        body.append(art)
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True, valign=Gtk.Align.CENTER)
        name = label(track.get("name", "Untitled"), "row-title")
        name.set_hexpand(True)
        name.set_wrap(True)
        name.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        name.set_lines(2)
        name.set_ellipsize(Pango.EllipsizeMode.END)
        artist = label(track_artists(track), "row-artist")
        artist.set_hexpand(True)
        artist.set_ellipsize(Pango.EllipsizeMode.END)
        info.append(name)
        info.append(artist)
        body.append(info)
        duration = label(timecode((track.get("duration_ms") or 0) / 1000), "row-duration")
        duration.set_xalign(1)
        duration.set_width_chars(5)
        body.append(duration)
        download = icon_button("folder-download-symbolic", "Save for offline",
                               lambda _button=None, value=track: self.toggle_audio_download(value), 15)
        download.add_css_class("row-download")
        body.append(download)
        row.download_button = download
        row.download_icon = download.get_child()
        self.track_rows.setdefault(track.get("id"), []).append(row)
        row.set_child(body)
        row.set_tooltip_text(name.get_text() + " · " + artist.get_text())
        self.load_cover(art, track_images(track), self.track_art_scope)
        return row

    def search_tracks(self, *_):
        query = self.search.get_text().strip()
        if query:
            self.load_query(query)

    def row_activated(self, _box, row):
        if not getattr(self, "visible_tracks", None):
            return
        self.queue = self.visible_tracks.copy()
        self.queue_index = row.get_index()
        self.play_track(self.queue[self.queue_index])

    def start_audio(self, track, url, origin="stream"):
        self.pending_track = False
        self.track = track
        self.duration = max(1, (track.get("duration_ms") or 1) / 1000)
        self.seek.set_range(0, self.duration)
        self.set_metadata(track["name"], track_artists(track))
        self.play_button.set_sensitive(True)
        self.playback_origin = origin
        self.update_like()
        self.update_download_button()
        self.update_playing_row()
        self.audio.load(url)
        self.prefetch_next()

    def play_track(self, track):
        self.send("stop")
        self.demo = False
        self.pending_track = True
        self.playing = False
        self.playback_origin = None
        self.track = track
        audio_generation, lyric_generation = self.requests.start_track()
        self.position = 0
        self.duration = max(1, (track.get("duration_ms") or 1) / 1000)
        self.set_metadata(track["name"], track_artists(track))
        self.play_button.set_sensitive(False)
        self.update_playing_row()
        self.update_download_button()
        self.render_lyrics([], {"source": "loading"})
        size = max(self.player_art.target, self.lyrics_art.target)
        url = best_image(track_images(track), size)
        self.current_art_url = url
        self.player_art.clear_art()
        self.lyrics_art.clear_art()
        self.lyrics_backdrop.set_paintable(None)
        if url:
            def show(texture):
                if url == self.current_art_url:
                    self.player_art.set_texture(texture)
                    self.lyrics_art.set_texture(texture)
                    self.lyrics_backdrop.set_paintable(texture)
            self.load_artwork(url, show, size)
        try:
            cached = audio_store.lookup(track)
        except Exception:
            cached = None
        if cached:
            self.status("Starting offline audio…" if cached["pinned"] else "Starting cached audio…")
            self.start_audio(track, cached["path"].as_uri(), "download" if cached["pinned"] else "cache")
            if self.requests.lyrics_current(lyric_generation):
                self.load_track_lyrics(track, lyric_generation)
            return
        self.status("Resolving a full YouTube Music stream…")
        def ready(result):
            if not self.requests.audio_current(audio_generation):
                return
            resolved, stream = result
            self.start_audio(resolved, stream, "stream")
            if self.requests.lyrics_current(lyric_generation):
                self.load_track_lyrics(resolved, lyric_generation)
        def failed(message):
            if self.requests.audio_current(audio_generation):
                self.pending_track = False
                self.render_lyrics([], {"source": "unavailable"})
                self.status(message)
        self.task(lambda: music_resolve(track), ready, failed)

    def load_track_lyrics(self, track, generation):
        def done(result):
            if self.requests.lyrics_current(generation):
                self.render_lyrics(*result)
        def failed(_message):
            if self.requests.lyrics_current(generation):
                self.render_lyrics([], {"source": "unavailable"})
                self.credits.set_text("No exact lyrics match. Import a TTML/LRC file for this recording.")
                self.credits.set_visible(True)
        self.task(lambda: metadata_lyrics(track), done, failed)

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
        self.update_liked_summary()

    def render_lyrics(self, lines, body):
        self.lines = lines
        self.active_line = -1
        self.active_lines = set()
        self.scroll_frame_time = None
        self.lyric_word_time = 0
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
            widget.set_max_width_chars(40)
            widget.set_halign(Gtk.Align.END if line.opposite else Gtk.Align.START)
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
        self.requests.start_track()
        self.demo = True
        self.pending_track = False
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
        self.lyrics_backdrop.set_paintable(None)
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

    def stop_lyric_frame(self):
        callback_id, self.lyric_frame_id = self.lyric_frame_id, None
        if callback_id is not None and hasattr(self, "window"):
            self.window.remove_tick_callback(callback_id)

    def lyric_frame_tick(self, _widget, frame_clock, _user_data=None):
        if self.closed:
            self.lyric_frame_id = None
            return False
        if (not hasattr(self, "pages") or self.pages.get_visible_child_name() != "lyrics"
                or not self.window.get_mapped() or self.window.is_suspended()):
            return True
        self.update_lyrics(self.current_position(), frame_clock)
        return True

    def update_lyrics(self, position, frame_clock):
        active_lines = {i for i, line in enumerate(self.lines) if 0 <= line.start <= position < line.end}
        active = next((i for i in sorted(active_lines) if not self.lines[i].background), min(active_lines, default=-1))
        changed = active != self.active_line
        if changed:
            self.active_line = active
            for index, widget in enumerate(self.lyric_labels):
                near = active >= 0 and index != active and abs(index - active) <= 1 and not self.lines[index].background
                if near:
                    widget.add_css_class("near-line")
                else:
                    widget.remove_css_class("near-line")
        for index in self.active_lines - active_lines:
            widget = self.lyric_labels[index]
            widget.remove_css_class("active-line")
            widget.set_text(self.lines[index].text)
        self.active_lines = active_lines
        now = frame_clock.get_frame_time()
        refresh_words = changed or now - self.lyric_word_time >= 16_000
        if refresh_words:
            self.lyric_word_time = now
        for index in active_lines:
            line, widget = self.lines[index], self.lyric_labels[index]
            widget.add_css_class("active-line")
            if line.words and refresh_words:
                def shade(word):
                    progress = max(0, min(1, (position - word.start) / max(0.01, word.end - word.start)))
                    return "#" + "".join(f"{round(a + (b-a)*progress):02x}" for a, b in zip((118, 118, 124), (255, 255, 255)))
                markup = "".join('<span foreground="' + shade(word) + '">' + html.escape(word.text) + '</span>' for word in line.words)
                widget.set_markup(markup)
            if changed and index == active:
                target = widget.get_allocation().y - self.lyric_scroll.get_height() * 0.40
                self.scroll_target = max(0, target)
        if changed and active < 0 and hasattr(self, "scroll_target"):
            del self.scroll_target
            self.scroll_frame_time = None
        if not hasattr(self, "scroll_target"):
            return
        adjustment = self.lyric_scroll.get_vadjustment()
        target = min(self.scroll_target, max(0, adjustment.get_upper() - adjustment.get_page_size()))
        delta = target - adjustment.get_value()
        if not self.animations or abs(delta) < 0.5:
            adjustment.set_value(target)
            del self.scroll_target
            self.scroll_frame_time = None
            return
        previous, self.scroll_frame_time = self.scroll_frame_time, now
        if previous is None:
            return
        elapsed = max(0.001, min(0.1, (now - previous) / 1_000_000))
        adjustment.set_value(adjustment.get_value() + delta * (1 - math.exp(-elapsed / 0.18)))

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
        if getattr(self, "google_login_window", None) is not None:
            self.google_login_cancel.set()
            self.google_login_window.destroy()
            self.google_login_window = None
        self.closed = True
        self.stop_lyric_frame()
        self.stop_engine()
        self.audio.close()
        if getattr(self, "mpris", None):
            self.mpris.close()
        return False


if __name__ == "__main__":
    Ember().run()
