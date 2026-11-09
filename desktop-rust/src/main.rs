mod app;
mod artwork;
mod bridge;
mod cache;
mod config;
mod icons;
mod library;
mod lyrics;
mod model;
mod playback;
mod player;
mod queue;

fn main() -> gtk4::glib::ExitCode {
    app::run()
}
