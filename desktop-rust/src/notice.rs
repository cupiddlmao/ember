use std::cell::RefCell;
use std::rc::Rc;
use std::time::Duration;

use gtk4::glib;
use gtk4::prelude::*;

const SUCCESS_DURATION: Duration = Duration::from_secs(4);

pub fn dismiss_after(error: bool) -> Option<Duration> {
    if error {
        None
    } else {
        Some(SUCCESS_DURATION)
    }
}

/// Bottom-left transient feedback. Errors persist until replaced; success
/// messages dismiss themselves after four seconds.
pub struct Notice {
    pub widget: gtk4::Box,
    label: gtk4::Label,
    spinner: gtk4::Spinner,
    timeout: Rc<RefCell<Option<glib::SourceId>>>,
}

impl Notice {
    pub fn new() -> Self {
        let widget = gtk4::Box::new(gtk4::Orientation::Horizontal, 8);
        widget.add_css_class("notice");
        widget.set_halign(gtk4::Align::Start);
        widget.set_valign(gtk4::Align::End);
        widget.set_margin_start(16);
        widget.set_margin_bottom(16);
        widget.set_visible(false);

        let spinner = gtk4::Spinner::new();
        spinner.set_size_request(14, 14);
        spinner.set_visible(false);

        let label = gtk4::Label::new(None);
        label.add_css_class("notice-label");
        label.set_ellipsize(gtk4::pango::EllipsizeMode::End);
        label.set_max_width_chars(80);

        widget.append(&spinner);
        widget.append(&label);

        Self {
            widget,
            label,
            spinner,
            timeout: Rc::new(RefCell::new(None)),
        }
    }

    fn clear_timeout(&self) {
        if let Some(id) = self.timeout.borrow_mut().take() {
            id.remove();
        }
    }

    /// Exposes the message to assistive technology: assertive for errors,
    /// polite for progress and success.
    fn announce(&self, text: &str, error: bool) {
        self.widget
            .update_property(&[gtk4::accessible::Property::Label(text)]);
        self.widget.announce(
            text,
            if error {
                gtk4::AccessibleAnnouncementPriority::High
            } else {
                gtk4::AccessibleAnnouncementPriority::Low
            },
        );
    }

    pub fn show(&self, text: &str, error: bool) {
        self.clear_timeout();
        self.spinner.stop();
        self.spinner.set_visible(false);
        self.label.set_text(text);
        self.label.set_tooltip_text(Some(text));
        self.announce(text, error);
        if error {
            self.widget.add_css_class("error");
        } else {
            self.widget.remove_css_class("error");
        }
        self.widget.set_visible(true);

        if let Some(duration) = dismiss_after(error) {
            let widget = self.widget.clone();
            let timeout = self.timeout.clone();
            let id = glib::timeout_add_local(duration, move || {
                *timeout.borrow_mut() = None;
                widget.set_visible(false);
                glib::ControlFlow::Break
            });
            *self.timeout.borrow_mut() = Some(id);
        }
    }

    /// In-flight work never auto-dismisses; the next `show` replaces it.
    pub fn show_progress(&self, text: &str) {
        self.clear_timeout();
        self.label.set_text(text);
        self.label.set_tooltip_text(Some(text));
        self.announce(text, false);
        self.widget.remove_css_class("error");
        self.spinner.set_visible(true);
        self.spinner.start();
        self.widget.set_visible(true);
    }

    pub fn hide(&self) {
        self.clear_timeout();
        self.spinner.stop();
        self.spinner.set_visible(false);
        self.widget.set_visible(false);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn errors_persist_and_success_dismisses() {
        assert_eq!(dismiss_after(true), None);
        assert_eq!(dismiss_after(false), Some(Duration::from_secs(4)));
    }
}
