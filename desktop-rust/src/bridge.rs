use crate::config::BridgeConfig;
use crate::lyrics::LyricsData;
use crate::model::{MAX_SEARCH_TRACKS, ResolveData, Track};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::fmt;
use std::io::{self, Read, Write};
use std::process::{Child, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::thread;
use std::time::{Duration, Instant};

pub const MAX_QUERY_CHARS: usize = 200;
pub const MAX_OUTPUT_BYTES: usize = 10 * 1024 * 1024;
pub const BRIDGE_TIMEOUT: Duration = Duration::from_secs(35);
const POLL_INTERVAL: Duration = Duration::from_millis(20);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BridgeError {
    code: &'static str,
    message: String,
}

impl BridgeError {
    pub fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }

    pub fn code(&self) -> &'static str {
        self.code
    }

    pub fn message(&self) -> &str {
        &self.message
    }

    fn cancelled() -> Self {
        Self::new("cancelled", "The music request was cancelled.")
    }

    fn invalid_response() -> Self {
        Self::new("bridge", "The music bridge returned an invalid response.")
    }

    fn timeout() -> Self {
        Self::new(
            "timeout",
            "The music service took too long to respond. Try again.",
        )
    }

    fn output_too_large() -> Self {
        Self::new("bridge", "The music bridge returned too much data.")
    }

    fn exited() -> Self {
        Self::new(
            "bridge",
            "The Python music bridge exited before returning a result.",
        )
    }
}

impl fmt::Display for BridgeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for BridgeError {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum JobKind {
    Search,
    Resolve,
    Lyrics,
    Cache,
    Library,
}

#[derive(Debug)]
pub enum BridgePayload {
    Search(Vec<Track>),
    Resolve(ResolveData),
    Lyrics(LyricsData),
    Cache { cleared: bool },
    Library(crate::library::LibraryState),
}

#[derive(Debug)]
pub struct JobResult {
    pub kind: JobKind,
    pub seq: u64,
    pub result: Result<BridgePayload, BridgeError>,
}

pub fn normalize_query(raw: &str) -> Result<String, BridgeError> {
    let query = raw.trim();
    if query.is_empty() {
        return Err(BridgeError::new("input", "Enter a song or artist."));
    }
    if query.chars().count() > MAX_QUERY_CHARS {
        return Err(BridgeError::new(
            "input",
            format!("Search is limited to {MAX_QUERY_CHARS} characters."),
        ));
    }
    if query.chars().any(char::is_control) {
        return Err(BridgeError::new(
            "input",
            "The search contains unsupported characters.",
        ));
    }
    Ok(query.to_owned())
}

pub fn sanitize_message(value: &str) -> String {
    let mut text = value.to_owned();
    loop {
        let lower = text.to_ascii_lowercase();
        let start = lower.find("https://").or_else(|| lower.find("http://"));
        let Some(start) = start else {
            break;
        };
        let end = text[start..]
            .find(char::is_whitespace)
            .map(|offset| start + offset)
            .unwrap_or(text.len());
        text.replace_range(start..end, "[link removed]");
    }

    for key in [
        "token",
        "signature",
        "sig",
        "key",
        "auth",
        "password",
        "secret",
    ] {
        const REDACTED: &str = "[redacted]";
        let needle = format!("{key}=");
        let mut search_start = 0;
        while search_start < text.len() {
            let lower = text.to_ascii_lowercase();
            let Some(relative_start) = lower[search_start..].find(&needle) else {
                break;
            };
            let start = search_start + relative_start;
            let value_start = start + needle.len();
            if text[value_start..].starts_with(REDACTED) {
                search_start = value_start + REDACTED.len();
                continue;
            }
            let value_end = text[value_start..]
                .find(|character: char| {
                    character.is_whitespace() || character == '&' || character == ']'
                })
                .map(|offset| value_start + offset)
                .unwrap_or(text.len());
            if value_end <= value_start {
                let skip = text[value_start..]
                    .chars()
                    .next()
                    .map(char::len_utf8)
                    .unwrap_or(0);
                if skip == 0 {
                    break;
                }
                search_start = value_start + skip;
                continue;
            }
            text.replace_range(value_start..value_end, REDACTED);
            search_start = value_start + REDACTED.len();
        }
    }

    let cleaned: String = text
        .chars()
        .map(|character| {
            if character.is_control() {
                ' '
            } else {
                character
            }
        })
        .collect();
    let mut cleaned = cleaned.split_whitespace().collect::<Vec<_>>().join(" ");
    if cleaned.chars().count() > 240 {
        cleaned = cleaned.chars().take(239).collect::<String>() + "…";
    }
    cleaned
}

#[derive(Serialize)]
#[serde(tag = "action", rename_all = "snake_case")]
enum Request<'a> {
    Search { query: &'a str },
    Resolve { track: &'a Track },
    Lyrics { track: &'a Track },
    Cache { track: &'a Track },
    ClearCache,
    Library { command: &'a Value },
}

#[derive(Deserialize)]
struct Envelope {
    ok: bool,
    #[serde(default)]
    data: Option<Value>,
    #[serde(default)]
    error: Option<RawError>,
}

#[derive(Deserialize)]
struct RawError {
    #[serde(default)]
    code: String,
    #[serde(default)]
    message: String,
}

#[derive(Deserialize)]
struct SearchData {
    tracks: Vec<Track>,
}

fn decode_error(raw: Option<RawError>) -> BridgeError {
    let Some(raw) = raw else {
        return BridgeError::invalid_response();
    };
    let code = match raw.code.as_str() {
        "input" => "input",
        "dependencies" => "dependencies",
        "unavailable" => "unavailable",
        "lyrics" => "lyrics",
        "timeout" => "timeout",
        _ => "bridge",
    };
    let message = sanitize_message(&raw.message);
    if message.is_empty() {
        BridgeError::new(code, "The music request could not be completed.")
    } else {
        BridgeError::new(code, message)
    }
}

fn parse_envelope(bytes: &[u8]) -> Result<Envelope, BridgeError> {
    if bytes.len() > MAX_OUTPUT_BYTES {
        return Err(BridgeError::output_too_large());
    }
    serde_json::from_slice(bytes).map_err(|_| BridgeError::invalid_response())
}

pub fn decode_search(bytes: &[u8]) -> Result<Vec<Track>, BridgeError> {
    let envelope = parse_envelope(bytes)?;
    if !envelope.ok {
        return Err(decode_error(envelope.error));
    }
    let data = envelope.data.ok_or_else(BridgeError::invalid_response)?;
    let parsed: SearchData =
        serde_json::from_value(data).map_err(|_| BridgeError::invalid_response())?;
    if parsed.tracks.len() > MAX_SEARCH_TRACKS {
        return Err(BridgeError::invalid_response());
    }
    let mut tracks = Vec::with_capacity(parsed.tracks.len());
    for track in parsed.tracks {
        tracks.push(
            track
                .validate()
                .map_err(|_| BridgeError::invalid_response())?,
        );
    }
    Ok(tracks)
}

pub fn decode_resolve(bytes: &[u8]) -> Result<ResolveData, BridgeError> {
    let envelope = parse_envelope(bytes)?;
    if !envelope.ok {
        return Err(decode_error(envelope.error));
    }
    let data = envelope.data.ok_or_else(BridgeError::invalid_response)?;
    let parsed: ResolveData =
        serde_json::from_value(data).map_err(|_| BridgeError::invalid_response())?;
    parsed
        .validate()
        .map_err(|_| BridgeError::invalid_response())
}

pub fn decode_lyrics(bytes: &[u8]) -> Result<LyricsData, BridgeError> {
    let envelope = parse_envelope(bytes)?;
    if !envelope.ok {
        return Err(decode_error(envelope.error));
    }
    let data = envelope.data.ok_or_else(BridgeError::invalid_response)?;
    let parsed: LyricsData =
        serde_json::from_value(data).map_err(|_| BridgeError::invalid_response())?;
    parsed
        .validate()
        .map_err(|_| BridgeError::invalid_response())
}

fn encode_request(request: &Request<'_>) -> Result<Vec<u8>, BridgeError> {
    serde_json::to_vec(request)
        .map_err(|_| BridgeError::new("bridge", "The music request could not be encoded."))
}

#[derive(Clone, Default)]
struct ProcessSlot {
    child: Arc<Mutex<Option<Child>>>,
}

impl ProcessSlot {
    fn install(&self, child: Child) {
        let previous = self
            .child
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .replace(child);
        if let Some(mut previous) = previous {
            terminate_group(previous.id());
            let _ = previous.kill();
            let _ = previous.wait();
        }
    }

    fn try_wait(&self) -> io::Result<Option<ExitStatus>> {
        let mut child = self
            .child
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        match child.as_mut() {
            Some(child) => child.try_wait(),
            None => Ok(None),
        }
    }

    fn clear(&self) {
        self.child
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .take();
    }

    fn cancel(&self) {
        let child = self
            .child
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .take();
        if let Some(mut child) = child {
            terminate_group(child.id());
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn terminate_group(pid: u32) {
    #[cfg(unix)]
    unsafe {
        // Each bridge process starts a new session, so its PID is its PGID.
        libc::kill(-(pid as i32), libc::SIGKILL);
    }
    #[cfg(not(unix))]
    let _ = pid;
}

fn add_process_group(command: &mut std::process::Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // The synchronous closure only calls async-signal-safe setsid().
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    Err(io::Error::last_os_error())
                } else {
                    Ok(())
                }
            });
        }
    }
}

fn spawn_error(error: io::Error) -> BridgeError {
    match error.kind() {
        io::ErrorKind::NotFound => BridgeError::new(
            "python",
            "The checkout Python environment is missing. Run `bash run.sh` once from the checkout.",
        ),
        io::ErrorKind::PermissionDenied => BridgeError::new(
            "python",
            "The checkout Python environment is not executable. Check the checkout environment.",
        ),
        _ => BridgeError::new(
            "python",
            "The checkout Python environment could not be started.",
        ),
    }
}

fn read_limited(mut input: impl Read, exceeded: &AtomicBool) -> Vec<u8> {
    let mut output = Vec::new();
    let mut buffer = [0_u8; 8192];
    loop {
        match input.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => {
                if output.len().saturating_add(count) > MAX_OUTPUT_BYTES {
                    exceeded.store(true, Ordering::SeqCst);
                    break;
                }
                output.extend_from_slice(&buffer[..count]);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => break,
        }
    }
    output
}

fn run_bridge(
    config: &BridgeConfig,
    request: &[u8],
    slot: &ProcessSlot,
    generation: &AtomicU64,
    expected: u64,
    shutdown: &AtomicBool,
    timeout: Duration,
) -> Result<Vec<u8>, BridgeError> {
    if shutdown.load(Ordering::SeqCst) || generation.load(Ordering::SeqCst) != expected {
        return Err(BridgeError::cancelled());
    }

    let mut command = config.command();
    command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    add_process_group(&mut command);

    let mut child = command.spawn().map_err(spawn_error)?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| BridgeError::new("bridge", "The music bridge has no output pipe."))?;
    let mut stdin = child
        .stdin
        .take()
        .ok_or_else(|| BridgeError::new("bridge", "The music bridge has no input pipe."))?;
    slot.install(child);

    if shutdown.load(Ordering::SeqCst) || generation.load(Ordering::SeqCst) != expected {
        slot.cancel();
        return Err(BridgeError::cancelled());
    }

    let exceeded = Arc::new(AtomicBool::new(false));
    let reader_exceeded = Arc::clone(&exceeded);
    let reader = thread::Builder::new()
        .name("ember-bridge-output".to_owned())
        .spawn(move || read_limited(stdout, &reader_exceeded));
    let reader = match reader {
        Ok(reader) => reader,
        Err(_) => {
            slot.cancel();
            return Err(BridgeError::new(
                "bridge",
                "The music bridge output reader could not start.",
            ));
        }
    };

    let write_result = stdin
        .write_all(request)
        .and_then(|_| stdin.write_all(b"\n"));
    drop(stdin);
    if write_result.is_err() {
        slot.cancel();
        let _ = reader.join();
        return Err(BridgeError::exited());
    }

    let deadline = Instant::now() + timeout;
    let mut exit_status = None;
    let mut terminal_error = None;
    loop {
        if shutdown.load(Ordering::SeqCst) || generation.load(Ordering::SeqCst) != expected {
            terminal_error = Some(BridgeError::cancelled());
            slot.cancel();
            break;
        }
        if exceeded.load(Ordering::SeqCst) {
            terminal_error = Some(BridgeError::output_too_large());
            slot.cancel();
            break;
        }
        match slot.try_wait() {
            Ok(Some(status)) => {
                slot.clear();
                exit_status = Some(status);
                break;
            }
            Ok(None) => {}
            Err(_) => {
                terminal_error = Some(BridgeError::exited());
                slot.cancel();
                break;
            }
        }
        if Instant::now() >= deadline {
            terminal_error = Some(BridgeError::timeout());
            slot.cancel();
            break;
        }
        thread::sleep(POLL_INTERVAL);
    }

    let output = reader.join().map_err(|_| BridgeError::exited())?;
    if let Some(error) = terminal_error {
        return Err(error);
    }
    if exceeded.load(Ordering::SeqCst) {
        return Err(BridgeError::output_too_large());
    }
    if !exit_status.map(|status| status.success()).unwrap_or(false) {
        return Err(BridgeError::exited());
    }
    Ok(output)
}

#[derive(Debug)]
enum QueuedRequest {
    Search(String),
    Resolve(Track),
    Lyrics(Track),
    Cache(Track),
    ClearCache,
    Library(Value),
}

impl QueuedRequest {
    fn encode(&self) -> Result<Vec<u8>, BridgeError> {
        match self {
            Self::Search(query) => encode_request(&Request::Search { query }),
            Self::Resolve(track) => encode_request(&Request::Resolve { track }),
            Self::Lyrics(track) => encode_request(&Request::Lyrics { track }),
            Self::Cache(track) => encode_request(&Request::Cache { track }),
            Self::ClearCache => encode_request(&Request::ClearCache),
            Self::Library(command) => encode_request(&Request::Library { command }),
        }
    }
}

#[derive(Debug)]
struct QueuedJob {
    seq: u64,
    request: QueuedRequest,
}

struct WorkerControl {
    pending: Arc<Mutex<Option<QueuedJob>>>,
    wake: Arc<Condvar>,
    generation: Arc<AtomicU64>,
    slot: ProcessSlot,
}

impl WorkerControl {
    fn submit(&self, job: QueuedJob) {
        // Invalidate the running job and kill its process before publishing
        // the replacement. The worker can then never mistake the new child
        // for the request that was just cancelled.
        self.generation.store(job.seq, Ordering::SeqCst);
        self.slot.cancel();
        *self
            .pending
            .lock()
            .unwrap_or_else(|poison| poison.into_inner()) = Some(job);
        self.wake.notify_one();
    }

    fn cancel(&self) {
        self.generation.store(0, Ordering::SeqCst);
        *self
            .pending
            .lock()
            .unwrap_or_else(|poison| poison.into_inner()) = None;
        self.wake.notify_one();
        self.slot.cancel();
    }
}

struct Worker;

impl Worker {
    fn spawn(
        config: BridgeConfig,
        kind: JobKind,
        tx: mpsc::Sender<JobResult>,
        shutdown: Arc<AtomicBool>,
    ) -> Result<WorkerControl, BridgeError> {
        let pending = Arc::new(Mutex::new(None));
        let wake = Arc::new(Condvar::new());
        let generation = Arc::new(AtomicU64::new(0));
        let slot = ProcessSlot::default();

        let pending_thread = Arc::clone(&pending);
        let wake_thread = Arc::clone(&wake);
        let generation_thread = Arc::clone(&generation);
        let slot_thread = slot.clone();
        let shutdown_thread = Arc::clone(&shutdown);
        let name = match kind {
            JobKind::Search => "ember-bridge-search",
            JobKind::Resolve => "ember-bridge-resolve",
            JobKind::Lyrics => "ember-bridge-lyrics",
            JobKind::Cache => "ember-bridge-cache",
            JobKind::Library => "ember-bridge-library",
        };
        thread::Builder::new()
            .name(name.to_owned())
            .spawn(move || {
                worker_loop(
                    config,
                    kind,
                    tx,
                    pending_thread,
                    wake_thread,
                    generation_thread,
                    slot_thread,
                    shutdown_thread,
                );
            })
            .map_err(|_| BridgeError::new("bridge", "The music bridge worker could not start."))?;

        Ok(WorkerControl {
            pending,
            wake,
            generation,
            slot,
        })
    }
}

fn worker_loop(
    config: BridgeConfig,
    kind: JobKind,
    tx: mpsc::Sender<JobResult>,
    pending: Arc<Mutex<Option<QueuedJob>>>,
    wake: Arc<Condvar>,
    generation: Arc<AtomicU64>,
    slot: ProcessSlot,
    shutdown: Arc<AtomicBool>,
) {
    loop {
        let job = {
            let mut guard = pending.lock().unwrap_or_else(|poison| poison.into_inner());
            while guard.is_none() && !shutdown.load(Ordering::SeqCst) {
                guard = wake
                    .wait(guard)
                    .unwrap_or_else(|poison| poison.into_inner());
            }
            if shutdown.load(Ordering::SeqCst) {
                return;
            }
            guard.take().expect("pending job")
        };

        if generation.load(Ordering::SeqCst) != job.seq {
            continue;
        }

        let result = job
            .request
            .encode()
            .and_then(|encoded| {
                run_bridge(
                    &config,
                    &encoded,
                    &slot,
                    &generation,
                    job.seq,
                    &shutdown,
                    if kind == JobKind::Cache {
                        Duration::from_secs(180)
                    } else {
                        BRIDGE_TIMEOUT
                    },
                )
            })
            .and_then(|bytes| match kind {
                JobKind::Search => decode_search(&bytes).map(BridgePayload::Search),
                JobKind::Resolve => decode_resolve(&bytes).map(BridgePayload::Resolve),
                JobKind::Lyrics => decode_lyrics(&bytes).map(BridgePayload::Lyrics),
                JobKind::Library => parse_envelope(&bytes).and_then(|envelope| {
                    if !envelope.ok {
                        return Err(decode_error(envelope.error));
                    }
                    let state: crate::library::LibraryState = serde_json::from_value(
                        envelope.data.ok_or_else(BridgeError::invalid_response)?,
                    )
                    .map_err(|_| BridgeError::invalid_response())?;
                    state
                        .validate()
                        .map_err(|_| BridgeError::invalid_response())
                        .map(BridgePayload::Library)
                }),
                JobKind::Cache => parse_envelope(&bytes).and_then(|envelope| {
                    if envelope.ok {
                        Ok(BridgePayload::Cache {
                            cleared: envelope
                                .data
                                .as_ref()
                                .and_then(|data| data.get("cleared"))
                                .and_then(Value::as_bool)
                                .unwrap_or(false),
                        })
                    } else {
                        Err(decode_error(envelope.error))
                    }
                }),
            });

        let _ = tx.send(JobResult {
            kind,
            seq: job.seq,
            result,
        });
    }
}

pub struct BridgeService {
    search: WorkerControl,
    resolve: WorkerControl,
    lyrics: WorkerControl,
    cache: WorkerControl,
    library: WorkerControl,
    shutdown: Arc<AtomicBool>,
    next_sequence: AtomicU64,
}

impl BridgeService {
    pub fn new(config: BridgeConfig, tx: mpsc::Sender<JobResult>) -> Result<Self, BridgeError> {
        let shutdown = Arc::new(AtomicBool::new(false));
        let search = Worker::spawn(
            config.clone(),
            JobKind::Search,
            tx.clone(),
            Arc::clone(&shutdown),
        )?;
        let resolve = match Worker::spawn(
            config.clone(),
            JobKind::Resolve,
            tx.clone(),
            Arc::clone(&shutdown),
        ) {
            Ok(worker) => worker,
            Err(error) => {
                shutdown.store(true, Ordering::SeqCst);
                search.cancel();
                return Err(error);
            }
        };
        let lyrics = match Worker::spawn(
            config.clone(),
            JobKind::Lyrics,
            tx.clone(),
            Arc::clone(&shutdown),
        ) {
            Ok(worker) => worker,
            Err(error) => {
                shutdown.store(true, Ordering::SeqCst);
                search.cancel();
                resolve.cancel();
                return Err(error);
            }
        };
        let cache = match Worker::spawn(
            config.clone(),
            JobKind::Cache,
            tx.clone(),
            Arc::clone(&shutdown),
        ) {
            Ok(worker) => worker,
            Err(error) => {
                shutdown.store(true, Ordering::SeqCst);
                search.cancel();
                resolve.cancel();
                lyrics.cancel();
                return Err(error);
            }
        };
        let library = match Worker::spawn(config, JobKind::Library, tx, Arc::clone(&shutdown)) {
            Ok(worker) => worker,
            Err(error) => {
                shutdown.store(true, Ordering::SeqCst);
                search.cancel();
                resolve.cancel();
                lyrics.cancel();
                cache.cancel();
                return Err(error);
            }
        };
        Ok(Self {
            search,
            resolve,
            lyrics,
            cache,
            library,
            shutdown,
            next_sequence: AtomicU64::new(0),
        })
    }

    pub fn submit_search(&self, query: &str) -> Result<u64, BridgeError> {
        if self.shutdown.load(Ordering::SeqCst) {
            return Err(BridgeError::cancelled());
        }
        let query = normalize_query(query)?;
        let seq = self.next();
        self.search.submit(QueuedJob {
            seq,
            request: QueuedRequest::Search(query),
        });
        Ok(seq)
    }

    pub fn submit_resolve(&self, track: &Track) -> Result<u64, BridgeError> {
        if self.shutdown.load(Ordering::SeqCst) {
            return Err(BridgeError::cancelled());
        }
        let track = track.clone().validate().map_err(|_| {
            BridgeError::new("input", "The selected recording metadata is invalid.")
        })?;
        let seq = self.next();
        self.resolve.submit(QueuedJob {
            seq,
            request: QueuedRequest::Resolve(track),
        });
        Ok(seq)
    }

    pub fn submit_lyrics(&self, track: &Track) -> Result<u64, BridgeError> {
        if self.shutdown.load(Ordering::SeqCst) {
            return Err(BridgeError::cancelled());
        }
        let track = track.clone().validate().map_err(|_| {
            BridgeError::new("input", "The selected recording metadata is invalid.")
        })?;
        let seq = self.next();
        self.lyrics.submit(QueuedJob {
            seq,
            request: QueuedRequest::Lyrics(track),
        });
        Ok(seq)
    }

    pub fn submit_library(&self, command: Value) -> Result<u64, BridgeError> {
        if self.shutdown.load(Ordering::SeqCst) {
            return Err(BridgeError::cancelled());
        }
        let seq = self.next();
        self.library.submit(QueuedJob {
            seq,
            request: QueuedRequest::Library(command),
        });
        Ok(seq)
    }

    pub fn cache_track(&self, track: &Track) {
        if !self.shutdown.load(Ordering::SeqCst) && crate::cache::enabled() {
            self.cache.submit(QueuedJob {
                seq: self.next(),
                request: QueuedRequest::Cache(track.clone()),
            });
        }
    }

    pub fn clear_cache(&self) {
        if !self.shutdown.load(Ordering::SeqCst) {
            self.cache.submit(QueuedJob {
                seq: self.next(),
                request: QueuedRequest::ClearCache,
            });
        }
    }

    pub fn cancel_cache(&self) {
        self.cache.cancel();
    }

    pub fn cancel_resolve(&self) {
        self.resolve.cancel();
    }

    pub fn cancel_lyrics(&self) {
        self.lyrics.cancel();
    }

    pub fn shutdown(&self) {
        if self.shutdown.swap(true, Ordering::SeqCst) {
            return;
        }
        self.search.cancel();
        self.resolve.cancel();
        self.lyrics.cancel();
        self.cache.cancel();
        self.library.cancel();
    }

    fn next(&self) -> u64 {
        self.next_sequence.fetch_add(1, Ordering::SeqCst) + 1
    }
}

impl Drop for BridgeService {
    fn drop(&mut self) {
        self.shutdown();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::PermissionsExt;

    fn search_json() -> &'static [u8] {
        br#"{"ok":true,"data":{"tracks":[
            {"id":"dm4tkSNKfFI","uri":"https://music.youtube.com/watch?v=dm4tkSNKfFI","name":"Awake","artists":["Tycho"],"album":"Awake","duration_ms":284000},
            {"id":"abcdefghijk","uri":"https://music.youtube.com/watch?v=abcdefghijk","name":"A Walk","artists":["Tycho"],"album":"Dive","duration_ms":315000}
        ]}}"#
    }

    fn fake_checkout(script: &str) -> tempfile::TempDir {
        let root = tempfile::tempdir().expect("temp checkout");
        fs::write(root.path().join("music.py"), "# fake\n").expect("music.py");
        let bridge_dir = root.path().join("desktop-rust");
        fs::create_dir_all(&bridge_dir).expect("bridge dir");
        fs::write(bridge_dir.join("bridge.py"), "# fake bridge\n").expect("bridge.py");
        let venv = root.path().join(".venv/bin");
        fs::create_dir_all(&venv).expect("venv dir");
        let python = venv.join("python");
        fs::write(&python, script).expect("python script");
        fs::set_permissions(&python, fs::Permissions::from_mode(0o755)).expect("executable");
        root
    }

    #[test]
    fn decodes_fake_search_tracks() {
        let tracks = decode_search(search_json()).expect("valid search");
        assert_eq!(tracks.len(), 2);
        assert_eq!(tracks[0].name, "Awake");
        assert_eq!(tracks[0].artist_line(), "Tycho");
        assert_eq!(tracks[1].duration_text(), "5:15");
    }

    #[test]
    fn returns_structured_sanitized_errors() {
        let raw = br#"{"ok":false,"error":{"code":"unavailable","message":"No stream at https://rr1.googlevideo.com/audio?token=secret-token."}}"#;
        let error = decode_search(raw).expect_err("must be an error");
        assert_eq!(error.code(), "unavailable");
        assert!(error.message().contains("[link removed]"));
        assert!(!error.message().contains("googlevideo"));
        assert!(!error.message().contains("secret-token"));
    }

    #[test]
    fn rejects_malformed_and_unsafe_metadata() {
        assert_eq!(
            decode_search(br#"{"ok":true,"data":{"tracks":"nope"}}"#)
                .expect_err("must reject")
                .code(),
            "bridge"
        );
        let bad_url = br#"{"ok":true,"data":{"track":{"id":"dm4tkSNKfFI","uri":"https://music.youtube.com/watch?v=dm4tkSNKfFI","name":"Awake","artists":["Tycho"],"album":"Awake","duration_ms":284000},"stream_url":"https://evil.test/audio"}}"#;
        assert!(decode_resolve(bad_url).is_err());
    }

    #[test]
    fn decodes_lyrics_states_and_rejects_bad_timestamps() {
        let lyrics = br#"{"ok":true,"data":{"track_id":"dm4tkSNKfFI","source":"lrclib","kind":"synced","lines":[{"text":"Awake","start_ms":1000,"end_ms":2000}],"message":null}}"#;
        let decoded = decode_lyrics(lyrics).expect("valid lyrics");
        assert_eq!(decoded.lines.len(), 1);
        assert_eq!(decoded.active_index(1500), Some(0));

        let invalid = br#"{"ok":true,"data":{"track_id":"dm4tkSNKfFI","source":"lrclib","kind":"synced","lines":[{"text":"Awake","start_ms":2000,"end_ms":1000}],"message":null}}"#;
        assert!(decode_lyrics(invalid).is_err());
    }

    #[test]
    fn limits_query_length() {
        let error = normalize_query(&"a".repeat(MAX_QUERY_CHARS + 1)).expect_err("too long");
        assert_eq!(error.code(), "input");
        assert_eq!(normalize_query("  Tycho  ").expect("query"), "Tycho");
    }

    #[test]
    fn sanitizes_urls_and_token_parameters_even_without_upstream_help() {
        let message = sanitize_message(
            "failed https://x.googlevideo.com/a?token=abc&amp;signature=def password=hunter2",
        );
        assert!(message.contains("[link removed]"));
        assert!(!message.contains("abc"));
        assert!(!message.contains("hunter2"));

        let repeated = sanitize_message("token=first&token=second password=third");
        assert!(repeated.contains("token=[redacted]&token=[redacted]"));
        assert!(!repeated.contains("first"));
        assert!(!repeated.contains("second"));
        assert!(!repeated.contains("third"));
    }

    #[test]
    fn stops_reading_at_the_output_bound() {
        let exceeded = AtomicBool::new(false);
        let input = std::io::Cursor::new(vec![b'x'; MAX_OUTPUT_BYTES + 64]);
        let output = read_limited(input, &exceeded);
        assert!(output.len() <= MAX_OUTPUT_BYTES);
        assert!(exceeded.load(Ordering::SeqCst));
    }

    #[test]
    fn runs_a_fake_python_bridge_without_a_shell() {
        let root = fake_checkout(
            "#!/bin/sh\ncat >/dev/null\nprintf '%s\\n' '{\"ok\":true,\"data\":{\"tracks\":[]}}'\n",
        );
        let config = BridgeConfig::from_root(root.path()).expect("fake config");
        let generation = AtomicU64::new(7);
        let shutdown = AtomicBool::new(false);
        let slot = ProcessSlot::default();
        let output = run_bridge(
            &config,
            br#"{"action":"search","query":"fake"}"#,
            &slot,
            &generation,
            7,
            &shutdown,
            Duration::from_secs(2),
        )
        .expect("fake bridge output");
        assert!(decode_search(&output).expect("valid JSON").is_empty());
    }

    #[test]
    fn kills_a_hung_bridge_at_the_deadline() {
        let root = fake_checkout("#!/bin/sh\ncat >/dev/null\nsleep 3\n");
        let config = BridgeConfig::from_root(root.path()).expect("fake config");
        let generation = AtomicU64::new(9);
        let shutdown = AtomicBool::new(false);
        let slot = ProcessSlot::default();
        let started = Instant::now();
        let error = run_bridge(
            &config,
            br#"{"action":"search","query":"fake"}"#,
            &slot,
            &generation,
            9,
            &shutdown,
            Duration::from_millis(120),
        )
        .expect_err("must time out");
        assert_eq!(error.code(), "timeout");
        assert!(started.elapsed() < Duration::from_secs(2));
    }
}
