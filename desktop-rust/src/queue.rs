use crate::model::Track;

pub type EntryId = u64;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct QueueEntry {
    pub id: EntryId,
    pub track: Track,
}

#[derive(Debug, Default)]
pub struct Queue {
    entries: Vec<QueueEntry>,
    current: Option<usize>,
    next_id: u64,
}

impl Queue {
    fn allocate(&mut self) -> EntryId {
        if self.next_id == 0 {
            self.next_id = 1;
        }
        let id = self.next_id;
        self.next_id = self.next_id.saturating_add(1);
        id
    }

    fn entry(&mut self, track: &Track) -> QueueEntry {
        QueueEntry {
            id: self.allocate(),
            track: track.clone(),
        }
    }

    /// Activating a search result replaces the queue with the displayed
    /// results. The selected occurrence becomes current.
    pub fn replace_search(&mut self, tracks: &[Track], selected: usize) -> Option<QueueEntry> {
        self.entries = tracks
            .iter()
            .map(|track| {
                let id = self.allocate();
                QueueEntry {
                    id,
                    track: track.clone(),
                }
            })
            .collect();
        if self.entries.is_empty() {
            self.current = None;
            return None;
        }
        let selected = selected.min(self.entries.len() - 1);
        self.current = Some(selected);
        self.current().cloned()
    }

    /// Inserts after the current occurrence. With no current entry, the new
    /// pending occurrence becomes the first one and playback stays stopped.
    pub fn play_next(&mut self, track: &Track) -> QueueEntry {
        let entry = self.entry(track);
        let position = self
            .current
            .map(|index| index.saturating_add(1))
            .unwrap_or(0)
            .min(self.entries.len());
        self.entries.insert(position, entry.clone());
        entry
    }

    pub fn add(&mut self, track: &Track) -> QueueEntry {
        let entry = self.entry(track);
        self.entries.push(entry.clone());
        entry
    }

    pub fn current(&self) -> Option<&QueueEntry> {
        self.current.and_then(|index| self.entries.get(index))
    }

    pub fn current_id(&self) -> Option<EntryId> {
        self.current().map(|entry| entry.id)
    }

    #[cfg(test)]
    pub fn current_index(&self) -> Option<usize> {
        self.current
    }

    pub fn select(&mut self, id: EntryId) -> Option<QueueEntry> {
        let index = self.entries.iter().position(|entry| entry.id == id)?;
        self.current = Some(index);
        self.entries.get(index).cloned()
    }

    pub fn select_index(&mut self, index: usize) -> Option<QueueEntry> {
        if index >= self.entries.len() {
            return None;
        }
        self.current = Some(index);
        self.entries.get(index).cloned()
    }

    pub fn next_index(&self) -> Option<usize> {
        match self.current {
            Some(index) if index + 1 < self.entries.len() => Some(index + 1),
            None if !self.entries.is_empty() => Some(0),
            _ => None,
        }
    }

    pub fn previous_index(&self) -> Option<usize> {
        self.current.and_then(|index| index.checked_sub(1))
    }

    pub fn upcoming(&self) -> impl Iterator<Item = &QueueEntry> {
        let start = self.current.map(|index| index + 1).unwrap_or(0);
        self.entries.iter().skip(start)
    }

    pub fn upcoming_count(&self) -> usize {
        self.upcoming().count()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn remove_upcoming(&mut self, id: EntryId) -> bool {
        let Some(index) = self.entries.iter().position(|entry| entry.id == id) else {
            return false;
        };
        if self.current.is_some_and(|current| index <= current) {
            return false;
        }
        self.entries.remove(index);
        true
    }

    pub fn move_upcoming(&mut self, id: EntryId, offset: isize) -> bool {
        let Some(index) = self.entries.iter().position(|entry| entry.id == id) else {
            return false;
        };
        let lower = self.current.map(|current| current + 1).unwrap_or(0);
        if index < lower {
            return false;
        }
        let Some(target) = index.checked_add_signed(offset) else {
            return false;
        };
        if target < lower || target >= self.entries.len() {
            return false;
        }
        let entry = self.entries.remove(index);
        self.entries.insert(target, entry);
        true
    }

    pub fn clear_upcoming(&mut self) -> bool {
        let keep = self.current.map(|index| index + 1).unwrap_or(0);
        if keep >= self.entries.len() {
            return false;
        }
        self.entries.truncate(keep);
        true
    }

    #[cfg(test)]
    pub fn all_entries(&self) -> &[QueueEntry] {
        &self.entries
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn track(id: &str, name: &str) -> Track {
        Track {
            id: id.to_owned(),
            uri: format!("https://music.youtube.com/watch?v={id}"),
            name: name.to_owned(),
            artists: vec!["Artist".to_owned()],
            album: String::new(),
            duration_ms: 180_000,
            artwork_url: None,
        }
    }

    fn names(queue: &Queue) -> Vec<&str> {
        queue
            .all_entries()
            .iter()
            .map(|entry| entry.track.name.as_str())
            .collect()
    }

    #[test]
    fn duplicate_occurrences_keep_independent_ids() {
        let mut queue = Queue::default();
        let first = queue.add(&track("aaaaaaaaaaa", "Same"));
        let second = queue.add(&track("aaaaaaaaaaa", "Same"));
        let third = queue.add(&track("aaaaaaaaaaa", "Same"));
        assert_ne!(first.id, second.id);
        assert_ne!(second.id, third.id);
        assert!(queue.select(second.id).is_some());
        assert!(!queue.remove_upcoming(first.id));
        assert!(queue.remove_upcoming(third.id));
        assert_eq!(queue.all_entries().len(), 2);
        assert_eq!(queue.current_id(), Some(second.id));
    }

    #[test]
    fn play_next_is_inserted_at_the_front_and_latest_wins() {
        let mut queue = Queue::default();
        let first = queue.play_next(&track("aaaaaaaaaaa", "A"));
        let second = queue.play_next(&track("bbbbbbbbbbb", "B"));
        assert_eq!(names(&queue), ["B", "A"]);
        assert_eq!(queue.current_id(), None);
        assert_eq!(first.id, 1);
        assert_eq!(second.id, 2);

        queue.select(first.id);
        queue.play_next(&track("ccccccccccc", "C"));
        assert_eq!(names(&queue), ["B", "A", "C"]);
        assert_eq!(queue.current_index(), Some(1));
    }

    #[test]
    fn add_remove_and_clear_only_touch_upcoming_occurrences() {
        let mut queue = Queue::default();
        for (index, name) in ["A", "B", "C", "D"].into_iter().enumerate() {
            queue.add(&track(&format!("{:011}", index + 1), name));
        }
        queue.select_index(1);
        let c = queue.all_entries()[2].id;
        let b = queue.all_entries()[1].id;
        assert!(!queue.remove_upcoming(b));
        assert!(queue.remove_upcoming(c));
        assert_eq!(names(&queue), ["A", "B", "D"]);
        assert_eq!(
            queue.current().map(|entry| entry.track.name.as_str()),
            Some("B")
        );
        assert!(queue.clear_upcoming());
        assert_eq!(names(&queue), ["A", "B"]);
        assert!(!queue.clear_upcoming());
    }

    #[test]
    fn reorder_respects_upcoming_boundaries_and_keeps_current() {
        let mut queue = Queue::default();
        for (index, name) in ["A", "B", "C", "D"].into_iter().enumerate() {
            queue.add(&track(&format!("{:011}", index + 1), name));
        }
        queue.select_index(1);
        let d = queue.all_entries()[3].id;
        let c = queue.all_entries()[2].id;
        assert!(queue.move_upcoming(d, -1));
        assert_eq!(names(&queue), ["A", "B", "D", "C"]);
        assert_eq!(
            queue.current().map(|entry| entry.track.name.as_str()),
            Some("B")
        );
        assert!(!queue.move_upcoming(d, -1));
        assert!(!queue.move_upcoming(c, 1));
        assert!(!queue.move_upcoming(queue.all_entries()[0].id, 1));
    }

    #[test]
    fn next_and_previous_respect_boundaries_without_looping() {
        let mut queue = Queue::default();
        queue.replace_search(
            &[
                track("aaaaaaaaaaa", "A"),
                track("bbbbbbbbbbb", "B"),
                track("ccccccccccc", "C"),
            ],
            0,
        );
        assert_eq!(queue.previous_index(), None);
        assert_eq!(queue.next_index(), Some(1));
        queue.select_index(2);
        assert_eq!(queue.next_index(), None);
        assert_eq!(queue.previous_index(), Some(1));
        assert_eq!(queue.upcoming_count(), 0);
    }
}
