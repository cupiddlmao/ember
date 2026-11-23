mod app;
mod artwork;
mod bridge;
mod cache;
mod config;
mod fonts;
mod home;
mod icons;
mod lastfm;
mod library;
mod lyrics;
mod model;
mod motion;
mod mpris;
mod notice;
mod playback;
mod player;
mod queue;
mod responsive;
mod tint;
mod viewport;

fn main() -> gtk4::glib::ExitCode {
    if let Err(error) = fonts::install() {
        eprintln!("Ember: {error}");
    }
    app::run()
}
