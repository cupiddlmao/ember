use crate::model::valid_artwork_url;
use crate::tint::Tint;
use gtk4::gio;
use gtk4::prelude::*;
use gtk4::{gdk, glib};
use soup::prelude::*;
use std::cell::RefCell;
use std::collections::{HashMap, VecDeque};
use std::rc::Rc;

const MAX_CONCURRENT: usize = 2;
const MAX_BYTES: usize = 2 * 1024 * 1024;
const READ_CHUNK: usize = 32 * 1024;
const MAX_DIMENSION: u32 = 1024;
// Retain requests for a full 5000-song playlist plus other visible surfaces.
const PENDING_LIMIT: usize = 5128;
const WAITERS_LIMIT: usize = 64;
const CACHE_LIMIT: usize = 64;
const REQUEST_TIMEOUT_SECONDS: u32 = 15;

#[derive(Clone)]
pub struct ArtworkSlot {
    picture: glib::WeakRef<gtk4::Picture>,
    desired: Rc<RefCell<String>>,
    on_tint: Option<Rc<dyn Fn(Tint)>>,
}

impl ArtworkSlot {
    pub fn new(picture: &gtk4::Picture) -> Self {
        Self {
            picture: picture.downgrade(),
            desired: Rc::new(RefCell::new(String::new())),
            on_tint: None,
        }
    }

    pub fn with_tint(picture: &gtk4::Picture, on_tint: Rc<dyn Fn(Tint)>) -> Self {
        Self {
            picture: picture.downgrade(),
            desired: Rc::new(RefCell::new(String::new())),
            on_tint: Some(on_tint),
        }
    }

    pub fn clear(&self) {
        self.desired.replace(String::new());
        if let Some(picture) = self.picture.upgrade() {
            picture.set_paintable(None::<&gdk::Texture>);
        }
    }

    pub fn accepts(&self, url: &str) -> bool {
        self.desired.borrow().as_str() == url
    }

    fn mark(&self, url: &str) {
        self.desired.replace(url.to_owned());
    }

    fn is_current(&self, url: &str) -> bool {
        self.accepts(url) && self.picture.upgrade().is_some()
    }

    fn apply(&self, url: &str, texture: &gdk::Texture, tint: Option<Tint>) {
        if !self.is_current(url) {
            return;
        }
        if let Some(picture) = self.picture.upgrade() {
            picture.set_paintable(Some(texture));
        }
        if let (Some(callback), Some(tint)) = (self.on_tint.as_ref(), tint) {
            callback(tint);
        }
    }

    #[cfg(test)]
    pub fn for_test(url: &str) -> Self {
        Self {
            picture: glib::WeakRef::new(),
            desired: Rc::new(RefCell::new(url.to_owned())),
            on_tint: None,
        }
    }
}

#[derive(Clone)]
struct CachedArtwork {
    texture: gdk::Texture,
    tint: Option<Tint>,
}

struct TintWaiter {
    url: String,
    callback: Rc<dyn Fn(Tint)>,
}

pub struct ArtworkLoader {
    session: soup::Session,
    cache: HashMap<String, CachedArtwork>,
    pending: HashMap<String, Vec<ArtworkSlot>>,
    tint_waiters: HashMap<u64, TintWaiter>,
    queue: VecDeque<String>,
    active: HashMap<u64, gio::Cancellable>,
    next_request: u64,
    next_tint: u64,
    cancelled: bool,
}

impl Default for ArtworkLoader {
    fn default() -> Self {
        let session = soup::Session::new();
        session.set_timeout(REQUEST_TIMEOUT_SECONDS);
        session.set_user_agent("Ember/0.1");
        Self {
            session,
            cache: HashMap::new(),
            pending: HashMap::new(),
            tint_waiters: HashMap::new(),
            queue: VecDeque::new(),
            active: HashMap::new(),
            next_request: 0,
            next_tint: 0,
            cancelled: false,
        }
    }
}

impl ArtworkLoader {
    pub fn request(loader: &Rc<RefCell<Self>>, url: &str, slot: ArtworkSlot) {
        if !valid_artwork_url(url) {
            slot.clear();
            return;
        }
        slot.mark(url);
        let cached = loader.borrow().cache.get(url).cloned();
        if let Some(artwork) = cached {
            slot.apply(url, &artwork.texture, artwork.tint);
            return;
        }
        {
            let mut loader = loader.borrow_mut();
            if loader.cancelled {
                return;
            }
            loader.prune();
            loader.enqueue(url, slot);
        }
        Self::pump(loader.clone());
    }

    pub fn request_tint(
        loader: &Rc<RefCell<Self>>,
        url: &str,
        callback: Rc<dyn Fn(Tint)>,
    ) -> u64 {
        if !valid_artwork_url(url) {
            return 0;
        }
        let cached = {
            let loader = loader.borrow();
            if loader.cancelled {
                return 0;
            }
            loader.cache.get(url).cloned()
        };
        if let Some(artwork) = cached {
            if let Some(tint) = artwork.tint {
                callback(tint);
            }
            return 0;
        }
        let mut state = loader.borrow_mut();
        if state.cancelled {
            return 0;
        }
        state.prune();
        state.ensure_pending(url);
        let token = state.next_tint_token();
        state.tint_waiters.insert(
            token,
            TintWaiter {
                url: url.to_owned(),
                callback,
            },
        );
        drop(state);
        Self::pump(loader.clone());
        token
    }

    pub fn cancel_tint(loader: &Rc<RefCell<Self>>, token: u64) {
        if token == 0 {
            return;
        }
        if let Ok(mut loader) = loader.try_borrow_mut() {
            loader.cancel_tint_token(token);
        }
    }

    fn cancel_tint_token(&mut self, token: u64) {
        self.tint_waiters.remove(&token);
        self.prune();
    }

    fn next_tint_token(&mut self) -> u64 {
        self.next_tint = self.next_tint.wrapping_add(1);
        if self.next_tint == 0 {
            self.next_tint = 1;
        }
        self.next_tint
    }

    #[cfg(test)]
    fn begin_tint(&mut self, url: &str) -> u64 {
        let callback: Rc<dyn Fn(Tint)> = Rc::new(|_| {});
        let token = self.next_tint_token();
        self.tint_waiters.insert(
            token,
            TintWaiter {
                url: url.to_owned(),
                callback,
            },
        );
        self.ensure_pending(url);
        token
    }

    #[cfg(test)]
    fn pending_tint_waiters(&self) -> usize {
        self.tint_waiters.len()
    }

    fn ensure_pending(&mut self, url: &str) {
        if self.pending.contains_key(url) {
            return;
        }
        if self.pending.len() >= PENDING_LIMIT
            && let Some(oldest) = self.queue.pop_front()
        {
            self.pending.remove(&oldest);
            self.tint_waiters.retain(|_, waiter| waiter.url != oldest);
        }
        self.pending.insert(url.to_owned(), Vec::new());
        self.queue.push_back(url.to_owned());
    }

    fn enqueue(&mut self, url: &str, slot: ArtworkSlot) {
        self.ensure_pending(url);
        if let Some(waiters) = self.pending.get_mut(url) {
            // Replace repeat requests for the same picture rather than retaining clones.
            waiters.retain(|old| old.picture != slot.picture);
            if waiters.len() == WAITERS_LIMIT {
                waiters.remove(0);
            }
            waiters.push(slot);
        }
    }

    fn prune(&mut self) {
        for (url, waiters) in &mut self.pending {
            waiters.retain(|slot| slot.is_current(url));
        }
        self.queue.retain(|url| {
            let has_slots = self.pending.get(url).is_some_and(|slots| !slots.is_empty());
            let has_tint = self
                .tint_waiters
                .values()
                .any(|waiter| waiter.url.as_str() == url.as_str());
            if has_slots || has_tint {
                true
            } else {
                self.pending.remove(url);
                false
            }
        });
    }

    pub fn shutdown(&mut self) {
        self.cancelled = true;
        for cancellable in self.active.values() {
            cancellable.cancel();
        }
        self.active.clear();
        self.pending.clear();
        self.tint_waiters.clear();
        self.queue.clear();
        self.cache.clear();
    }

    fn pump(loader: Rc<RefCell<Self>>) {
        loop {
            let next = {
                let mut loader = loader.borrow_mut();
                loader.prune();
                if loader.cancelled || loader.active.len() >= MAX_CONCURRENT {
                    break;
                }
                let Some(url) = loader.queue.pop_front() else {
                    break;
                };
                loader.next_request = loader.next_request.wrapping_add(1);
                let id = loader.next_request;
                let cancellable = gio::Cancellable::new();
                loader.active.insert(id, cancellable.clone());
                (url, id, cancellable)
            };
            Self::load(loader.clone(), next.0, next.1, next.2);
        }
    }

    fn load(loader: Rc<RefCell<Self>>, url: String, id: u64, cancellable: gio::Cancellable) {
        let message = match soup::Message::new("GET", &url) {
            Ok(message) => message,
            Err(_) => {
                Self::finish(loader, &url, id, None);
                return;
            }
        };
        // Thumbnail URLs come from a fixed allowlist. Do not follow redirects
        // onto a host that the model checks never approved.
        message.set_flags(soup::MessageFlags::NO_REDIRECT);
        let response_message = message.clone();
        let session = loader.borrow().session.clone();
        let callback_loader = loader.clone();
        let callback_url = url;
        let read_cancellable = cancellable.clone();
        session.send_async(
            &message,
            glib::Priority::DEFAULT,
            Some(&cancellable),
            move |result| {
                if callback_loader.borrow().cancelled {
                    Self::finish(callback_loader, &callback_url, id, None);
                    return;
                }
                let stream = match result {
                    Ok(stream) => stream,
                    Err(_) => {
                        Self::finish(callback_loader, &callback_url, id, None);
                        return;
                    }
                };
                if response_message.status() != soup::Status::Ok {
                    Self::finish(callback_loader, &callback_url, id, None);
                    return;
                }
                if let Some(headers) = response_message.response_headers()
                    && headers.content_length() > MAX_BYTES as i64
                {
                    Self::finish(callback_loader, &callback_url, id, None);
                    return;
                }
                Self::read_chunk(
                    callback_loader,
                    callback_url,
                    id,
                    stream,
                    Vec::with_capacity(READ_CHUNK),
                    read_cancellable,
                );
            },
        );
    }

    fn read_chunk(
        loader: Rc<RefCell<Self>>,
        url: String,
        id: u64,
        stream: gio::InputStream,
        mut body: Vec<u8>,
        cancellable: gio::Cancellable,
    ) {
        let next_stream = stream.clone();
        let next_loader = loader.clone();
        let next_url = url.clone();
        let next_cancellable = cancellable.clone();
        stream.read_bytes_async(
            READ_CHUNK,
            glib::Priority::DEFAULT,
            Some(&cancellable),
            move |result| {
                let bytes = match result {
                    Ok(bytes) => bytes,
                    Err(_) => {
                        Self::finish(next_loader, &next_url, id, None);
                        return;
                    }
                };
                if bytes.is_empty() {
                    Self::decode(next_loader, &next_url, id, body);
                    return;
                }
                if !append_bounded(&mut body, &bytes, MAX_BYTES) {
                    Self::finish(next_loader, &next_url, id, None);
                    return;
                }
                Self::read_chunk(
                    next_loader,
                    next_url,
                    id,
                    next_stream,
                    body,
                    next_cancellable,
                );
            },
        );
    }

    fn decode(loader: Rc<RefCell<Self>>, url: &str, id: u64, body: Vec<u8>) {
        let wanted = {
            let loader = loader.borrow();
            loader
                .pending
                .get(url)
                .is_some_and(|slots| slots.iter().any(|slot| slot.is_current(url)))
                || loader
                    .tint_waiters
                    .values()
                    .any(|waiter| waiter.url.as_str() == url)
        };
        if body.is_empty() || !wanted || loader.borrow().cancelled {
            Self::finish(loader, url, id, None);
            return;
        }
        let url = url.to_owned();
        glib::spawn_future_local(async move {
            // Keep the request active until decoding completes, bounding worker count too.
            let decoded = gio::spawn_blocking(move || {
                decode_pixels(body).map(|(pixels, width, height)| {
                    let tint = crate::tint::from_rgba(&pixels, width, height);
                    (pixels, width, height, tint)
                })
            })
            .await
            .ok()
            .flatten();
            if loader.borrow().cancelled {
                Self::finish(loader, &url, id, None);
                return;
            }
            let artwork = decoded.map(|(pixels, width, height, tint)| {
                let texture = gdk::MemoryTexture::new(
                    width as i32,
                    height as i32,
                    gdk::MemoryFormat::R8g8b8a8,
                    &glib::Bytes::from_owned(pixels),
                    width as usize * 4,
                )
                .upcast::<gdk::Texture>();
                CachedArtwork { texture, tint }
            });
            Self::finish(loader, &url, id, artwork);
        });
    }

    fn finish(
        loader: Rc<RefCell<Self>>,
        url: &str,
        id: u64,
        artwork: Option<CachedArtwork>,
    ) {
        let (waiters, tint_waiters) = {
            let mut state = loader.borrow_mut();
            state.active.remove(&id);
            let waiters = state.pending.remove(url).unwrap_or_default();
            let tint_waiters: Vec<Rc<dyn Fn(Tint)>> = state
                .tint_waiters
                .iter()
                .filter(|(_, waiter)| waiter.url.as_str() == url)
                .map(|(_, waiter)| Rc::clone(&waiter.callback))
                .collect();
            state
                .tint_waiters
                .retain(|_, waiter| waiter.url.as_str() != url);
            if state.cancelled {
                return;
            }
            if let Some(artwork) = artwork.as_ref() {
                if state.cache.len() >= CACHE_LIMIT
                    && let Some(key) = state.cache.keys().next().cloned()
                {
                    state.cache.remove(&key);
                }
                state.cache.insert(url.to_owned(), artwork.clone());
            }
            (waiters, tint_waiters)
        };
        if let Some(artwork) = artwork {
            for slot in waiters {
                slot.apply(url, &artwork.texture, artwork.tint);
            }
            if let Some(tint) = artwork.tint {
                for callback in tint_waiters {
                    callback(tint);
                }
            }
        }
        Self::pump(loader);
    }
}

fn decode_pixels(body: Vec<u8>) -> Option<(Vec<u8>, u32, u32)> {
    if body.is_empty() || body.len() > MAX_BYTES {
        return None;
    }
    let mut reader = image::ImageReader::new(std::io::Cursor::new(body))
        .with_guessed_format()
        .ok()?;
    let mut limits = image::Limits::default();
    limits.max_image_width = Some(MAX_DIMENSION);
    limits.max_image_height = Some(MAX_DIMENSION);
    limits.max_alloc = Some(16 * 1024 * 1024);
    reader.limits(limits);
    let image = reader.decode().ok()?.into_rgba8();
    let (width, height) = image.dimensions();
    Some((image.into_raw(), width, height))
}

fn append_bounded(body: &mut Vec<u8>, chunk: &[u8], limit: usize) -> bool {
    if chunk.len() > limit.saturating_sub(body.len()) {
        return false;
    }
    body.extend_from_slice(chunk);
    true
}

/// Corner radius of a square cover, kept in step with `.playlist-card-cover`.
pub const COVER_RADIUS: f32 = 10.0;

mod square_cover {
    use super::COVER_RADIUS;
    use gtk4::subclass::prelude::*;
    use gtk4::{glib, prelude::*};

    #[derive(Default)]
    pub struct SquareCover;

    #[glib::object_subclass]
    impl ObjectSubclass for SquareCover {
        const NAME: &'static str = "EmberSquareCover";
        type Type = super::SquareCover;
        type ParentType = gtk4::Widget;
    }
    impl ObjectImpl for SquareCover {
        fn dispose(&self) {
            while let Some(child) = self.obj().first_child() {
                child.unparent();
            }
        }
    }
    impl WidgetImpl for SquareCover {
        fn request_mode(&self) -> gtk4::SizeRequestMode {
            gtk4::SizeRequestMode::HeightForWidth
        }
        fn measure(&self, orientation: gtk4::Orientation, for_size: i32) -> (i32, i32, i32, i32) {
            if orientation == gtk4::Orientation::Vertical && for_size >= 0 {
                (for_size, for_size, -1, -1)
            } else {
                (150, 150, -1, -1)
            }
        }
        fn size_allocate(&self, width: i32, _height: i32, baseline: i32) {
            if let Some(child) = self.obj().first_child() {
                child.allocate(width, width, baseline, None);
            }
        }
        fn snapshot(&self, snapshot: &gtk4::Snapshot) {
            let width = self.obj().width().max(0) as f32;
            // The collage is a grid of square images, so only this widget can
            // round the four outer corners; CSS radii on the tiles themselves
            // would round every inner edge as well.
            let rect = gtk4::graphene::Rect::new(0.0, 0.0, width, width);
            let radius = gtk4::graphene::Size::new(COVER_RADIUS, COVER_RADIUS);
            snapshot.push_rounded_clip(&gtk4::gsk::RoundedRect::new(
                rect, radius, radius, radius, radius,
            ));
            if let Some(child) = self.obj().first_child() {
                self.obj().snapshot_child(&child, snapshot);
            }
            snapshot.pop();
        }
    }
}

glib::wrapper! {
    pub struct SquareCover(ObjectSubclass<square_cover::SquareCover>)
        @extends gtk4::Widget,
        @implements gtk4::Accessible, gtk4::Buildable, gtk4::ConstraintTarget;
}

pub fn artwork_view(size: i32, glyph_size: i32) -> (gtk4::Overlay, gtk4::Picture) {
    let frame = gtk4::Overlay::new();
    frame.add_css_class("artwork-frame");
    frame.set_size_request(size, size);
    frame.set_halign(gtk4::Align::Start);
    frame.set_valign(gtk4::Align::Center);

    let placeholder = gtk4::Box::new(gtk4::Orientation::Vertical, 0);
    placeholder.add_css_class("artwork-placeholder");
    placeholder.set_halign(gtk4::Align::Fill);
    placeholder.set_valign(gtk4::Align::Fill);
    let glyph = gtk4::Image::from_icon_name("media-optical-symbolic");
    glyph.set_pixel_size(glyph_size);
    glyph.add_css_class("artwork-glyph");
    glyph.set_halign(gtk4::Align::Center);
    glyph.set_valign(gtk4::Align::Center);
    glyph.set_vexpand(true);
    placeholder.append(&glyph);
    frame.set_child(Some(&placeholder));

    let picture = gtk4::Picture::new();
    picture.add_css_class("artwork-picture");
    picture.set_can_shrink(true);
    picture.set_content_fit(gtk4::ContentFit::Cover);
    picture.set_halign(gtk4::Align::Fill);
    picture.set_valign(gtk4::Align::Fill);
    picture.set_size_request(size, size);
    frame.add_overlay(&picture);
    (frame, picture)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn empty_slot() -> ArtworkSlot {
        ArtworkSlot {
            picture: glib::WeakRef::new(),
            desired: Rc::new(RefCell::new(String::new())),
            on_tint: None,
        }
    }

    #[test]
    fn a_slot_only_accepts_its_latest_url() {
        let slot = ArtworkSlot::for_test("https://i.ytimg.com/a");
        assert!(slot.accepts("https://i.ytimg.com/a"));
        assert!(!slot.accepts("https://i.ytimg.com/b"));
    }

    #[test]
    fn cancelled_tint_request_is_dropped() {
        let mut loader = ArtworkLoader::default();
        let token = loader.begin_tint("https://i.ytimg.com/a");
        loader.cancel_tint_token(token);
        assert_eq!(loader.pending_tint_waiters(), 0);
    }

    #[test]
    fn backlog_is_bounded_and_dead_targets_are_pruned() {
        let mut loader = ArtworkLoader::default();
        for index in 0..PENDING_LIMIT * 3 {
            loader.enqueue(&format!("https://i.ytimg.com/{index}"), empty_slot());
        }
        assert_eq!(loader.pending.len(), PENDING_LIMIT);
        assert_eq!(loader.queue.len(), PENDING_LIMIT);
        assert!(!loader.pending.contains_key("https://i.ytimg.com/0"));
        loader.prune();
        assert!(loader.pending.is_empty());
        assert!(loader.queue.is_empty());
    }

    #[test]
    fn playlist_batches_do_not_discard_earlier_cover_requests() {
        let mut loader = ArtworkLoader::default();
        // Opening a playlist queues 100 rows; type-to-jump can queue all 5000.
        for index in 0..5000 {
            loader.enqueue(&format!("https://i.ytimg.com/vi/{index}/hqdefault.jpg"), empty_slot());
        }
        assert_eq!(loader.pending.len(), 5000);
        assert_eq!(loader.queue.len(), 5000);
        assert!(loader.pending.contains_key("https://i.ytimg.com/vi/0/hqdefault.jpg"));
        assert!(loader.pending.contains_key("https://i.ytimg.com/vi/99/hqdefault.jpg"));
    }

    #[test]
    fn repeated_targets_do_not_accumulate_waiters() {
        let mut loader = ArtworkLoader::default();
        let slot = empty_slot();
        for _ in 0..WAITERS_LIMIT * 3 {
            loader.enqueue("https://i.ytimg.com/test", slot.clone());
        }
        assert_eq!(loader.pending["https://i.ytimg.com/test"].len(), 1);
    }

    fn png(width: u32, height: u32) -> Vec<u8> {
        let mut encoded = std::io::Cursor::new(Vec::new());
        image::RgbaImage::new(width, height)
            .write_to(&mut encoded, image::ImageFormat::Png)
            .unwrap();
        encoded.into_inner()
    }

    #[test]
    fn decoding_rejects_oversized_dimensions_and_malformed_input() {
        let body = png(MAX_DIMENSION + 1, 1);
        assert!(body.len() < MAX_BYTES);
        assert!(decode_pixels(body).is_none());
        assert!(decode_pixels(png(1, MAX_DIMENSION + 1)).is_none());
        assert!(decode_pixels(vec![0; 100]).is_none());
        assert!(decode_pixels(vec![0; MAX_BYTES + 1]).is_none());
        let (pixels, width, height) = decode_pixels(png(2, 3)).unwrap();
        assert_eq!((width, height, pixels.len()), (2, 3, 24));
    }

    #[test]
    fn response_bodies_stop_at_the_byte_limit() {
        let mut body = vec![1, 2];
        assert!(append_bounded(&mut body, &[3, 4], 4));
        assert_eq!(body, vec![1, 2, 3, 4]);
        assert!(!append_bounded(&mut body, &[5], 4));
        assert_eq!(body, vec![1, 2, 3, 4]);
    }
}
