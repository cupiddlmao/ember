use crate::model::valid_artwork_url;
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
const PENDING_LIMIT: usize = 64;
const WAITERS_LIMIT: usize = 64;
const CACHE_LIMIT: usize = 64;
const REQUEST_TIMEOUT_SECONDS: u32 = 15;

#[derive(Clone)]
pub struct ArtworkSlot {
    picture: glib::WeakRef<gtk4::Picture>,
    desired: Rc<RefCell<String>>,
}

impl ArtworkSlot {
    pub fn new(picture: &gtk4::Picture) -> Self {
        Self {
            picture: picture.downgrade(),
            desired: Rc::new(RefCell::new(String::new())),
        }
    }

    pub fn clear(&self) {
        self.desired.replace(String::new());
        if let Some(picture) = self.picture.upgrade() {
            picture.set_paintable(None::<&gdk::Texture>);
        }
    }

    fn mark(&self, url: &str) {
        self.desired.replace(url.to_owned());
    }

    fn is_current(&self, url: &str) -> bool {
        self.desired.borrow().as_str() == url && self.picture.upgrade().is_some()
    }

    fn apply(&self, url: &str, texture: &gdk::Texture) {
        if self.is_current(url)
            && let Some(picture) = self.picture.upgrade()
        {
            picture.set_paintable(Some(texture));
        }
    }
}

pub struct ArtworkLoader {
    session: soup::Session,
    cache: HashMap<String, gdk::Texture>,
    pending: HashMap<String, Vec<ArtworkSlot>>,
    queue: VecDeque<String>,
    active: HashMap<u64, gio::Cancellable>,
    next_request: u64,
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
            queue: VecDeque::new(),
            active: HashMap::new(),
            next_request: 0,
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
        if let Some(texture) = cached {
            slot.apply(url, &texture);
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

    fn enqueue(&mut self, url: &str, slot: ArtworkSlot) {
        if !self.pending.contains_key(url) {
            if self.pending.len() >= PENDING_LIMIT
                && let Some(oldest) = self.queue.pop_front()
            {
                self.pending.remove(&oldest);
            }
            self.pending.insert(url.to_owned(), Vec::new());
            self.queue.push_back(url.to_owned());
        }
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
            if self.pending.get(url).is_some_and(|slots| !slots.is_empty()) {
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
        let wanted = loader
            .borrow()
            .pending
            .get(url)
            .is_some_and(|slots| slots.iter().any(|slot| slot.is_current(url)));
        if body.is_empty() || !wanted || loader.borrow().cancelled {
            Self::finish(loader, url, id, None);
            return;
        }
        let url = url.to_owned();
        glib::spawn_future_local(async move {
            // Keep the request active until decoding completes, bounding worker count too.
            let decoded = gio::spawn_blocking(move || decode_pixels(body))
                .await
                .ok()
                .flatten();
            if loader.borrow().cancelled {
                Self::finish(loader, &url, id, None);
                return;
            }
            let texture = decoded.map(|(pixels, width, height)| {
                gdk::MemoryTexture::new(
                    width as i32,
                    height as i32,
                    gdk::MemoryFormat::R8g8b8a8,
                    &glib::Bytes::from_owned(pixels),
                    width as usize * 4,
                )
                .upcast::<gdk::Texture>()
            });
            Self::finish(loader, &url, id, texture);
        });
    }

    fn finish(loader: Rc<RefCell<Self>>, url: &str, id: u64, texture: Option<gdk::Texture>) {
        let waiters = {
            let mut loader = loader.borrow_mut();
            loader.active.remove(&id);
            let waiters = loader.pending.remove(url).unwrap_or_default();
            if loader.cancelled {
                return;
            }
            if let Some(texture) = texture.as_ref() {
                if loader.cache.len() >= CACHE_LIMIT
                    && let Some(key) = loader.cache.keys().next().cloned()
                {
                    loader.cache.remove(&key);
                }
                loader.cache.insert(url.to_owned(), texture.clone());
            }
            waiters
        };
        if let Some(texture) = texture {
            for slot in waiters {
                slot.apply(url, &texture);
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
        }
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
