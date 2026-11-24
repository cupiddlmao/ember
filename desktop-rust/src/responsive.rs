//! Width-aware container for the player titlebar.

use std::cell::{Cell, RefCell};
use std::rc::Rc;

use gtk4::glib;
use gtk4::prelude::*;
use gtk4::subclass::prelude::*;

pub const COMPACT_WIDTH: i32 = 900;

/// Whether the player must drop secondary controls at this window width.
pub fn compact_player(width: i32) -> bool {
    width < COMPACT_WIDTH
}

mod imp {
    use super::*;

    type WidthCallback = Rc<dyn Fn(i32)>;

    #[derive(Default)]
    pub struct ResponsiveBar {
        pub(crate) callbacks: RefCell<Vec<WidthCallback>>,
        last_width: Cell<i32>,
        pub(crate) child: RefCell<Option<gtk4::Box>>,
    }

    #[glib::object_subclass]
    impl ObjectSubclass for ResponsiveBar {
        const NAME: &'static str = "EmberResponsiveBar";
        type Type = super::ResponsiveBar;
        type ParentType = gtk4::Widget;
    }

    impl ObjectImpl for ResponsiveBar {
        fn dispose(&self) {
            if let Some(child) = self.child.borrow_mut().take() {
                child.unparent();
            }
        }
    }

    impl WidgetImpl for ResponsiveBar {
        fn measure(&self, orientation: gtk4::Orientation, for_size: i32) -> (i32, i32, i32, i32) {
            if let Some(child) = self.child.borrow().as_ref() {
                child.measure(orientation, for_size)
            } else {
                (0, 0, -1, -1)
            }
        }

        fn size_allocate(&self, width: i32, height: i32, baseline: i32) {
            if let Some(child) = self.child.borrow().as_ref() {
                child.allocate(width, height, baseline, None);
            }
            if self.last_width.get() == width {
                return;
            }
            self.last_width.set(width);
            let callbacks: Vec<_> = self.callbacks.borrow().clone();
            for callback in callbacks {
                callback(width);
            }
        }

        fn snapshot(&self, snapshot: &gtk4::Snapshot) {
            if let Some(child) = self.child.borrow().as_ref() {
                self.obj().snapshot_child(child, snapshot);
            }
        }
    }
}

glib::wrapper! {
    pub struct ResponsiveBar(ObjectSubclass<imp::ResponsiveBar>)
        @extends gtk4::Widget,
        @implements gtk4::Accessible, gtk4::Buildable, gtk4::ConstraintTarget;
}

impl ResponsiveBar {
    pub fn new(orientation: gtk4::Orientation) -> Self {
        let bar: Self = glib::Object::new();
        let child = gtk4::Box::new(orientation, 0);
        child.add_css_class("responsive-bar");
        child.set_parent(&bar);
        bar.imp().child.replace(Some(child));
        bar
    }

    pub fn append(&self, widget: &impl IsA<gtk4::Widget>) {
        if let Some(child) = self.imp().child.borrow().as_ref() {
            child.append(widget);
        }
    }

    /// Registers a callback invoked whenever the allocated width changes.
    ///
    /// Reports the current width immediately when the bar has already been
    /// allocated, so a late registration cannot miss the initial layout.
    pub fn on_width(&self, callback: impl Fn(i32) + 'static) {
        let width = self.width();
        if width > 0 {
            callback(width);
        }
        self.imp().callbacks.borrow_mut().push(Rc::new(callback));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn compact_player_threshold() {
        assert!(compact_player(780));
        assert!(!compact_player(900));
        assert!(!compact_player(1180));
    }

    #[test]
    #[ignore = "requires a native GTK display; run with --ignored --test-threads=1"]
    fn reports_allocated_widths() {
        gtk4::init().expect("requires a native GTK display");
        assert_eq!(reported_width(1180), Some(1180));
        assert_eq!(reported_width(780), Some(780));
    }

    fn reported_width(wanted: i32) -> Option<i32> {
        let bar = ResponsiveBar::new(gtk4::Orientation::Vertical);
        bar.set_size_request(wanted, 120);
        let widths = Rc::new(RefCell::new(Vec::new()));
        let seen = widths.clone();
        bar.on_width(move |width| seen.borrow_mut().push(width));
        let window = gtk4::Window::new();
        window.set_default_size(wanted, 120);
        // A tiling window manager can override the toplevel width, so the bar
        // gets its size from a fixed container instead of the window.
        let fixed = gtk4::Fixed::new();
        fixed.put(&bar, 0.0, 0.0);
        window.set_child(Some(&fixed));
        window.present();
        let context = glib::MainContext::default();
        let started = std::time::Instant::now();
        while widths.borrow().last().copied() != Some(wanted)
            && started.elapsed() < std::time::Duration::from_secs(2)
        {
            while context.iteration(false) {}
            std::thread::sleep(std::time::Duration::from_millis(5));
        }
        let reported = widths.borrow().last().copied();
        window.close();
        reported
    }
}
