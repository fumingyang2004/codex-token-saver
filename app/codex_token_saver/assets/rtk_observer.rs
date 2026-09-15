//! Optional local observation only. No filter/guard/output decisions are changed.
use std::collections::HashMap;
use std::io::Write;
use std::sync::{LazyLock, Mutex, OnceLock};
use std::sync::atomic::{AtomicBool, Ordering};
use sha2::{Digest, Sha256};

const LIMIT: usize = 16 * 1024 * 1024;
static RAW: LazyLock<Mutex<HashMap<String, &'static str>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));
static CONFIG: OnceLock<Option<(std::ffi::OsString, String)>> = OnceLock::new();
static INCOMPLETE: AtomicBool = AtomicBool::new(false);

/// Runs first in main, before threads/children. Observer capability is private to
/// this RTK process; wrapped commands never inherit its file path or nonce.
pub fn initialize() {
    let config = std::env::var_os("CES_RTK_OBSERVATION_FILE")
        .zip(std::env::var("CES_RTK_INVOCATION_ID").ok());
    let _ = CONFIG.set(config);
    std::env::remove_var("CES_RTK_OBSERVATION_FILE");
    std::env::remove_var("CES_RTK_INVOCATION_ID");
}

pub fn incomplete() {
    INCOMPLETE.store(true, Ordering::Relaxed);
    if let Some(Some((path, _))) = CONFIG.get() { let _ = std::fs::remove_file(path); }
}

pub fn managed() -> bool {
    matches!(CONFIG.get(), Some(Some(_)))
}

fn enabled() -> bool {
    matches!(CONFIG.get(), Some(Some(_))) && !INCOMPLETE.load(Ordering::Relaxed)
}

fn hash(text: &str) -> String {
    format!("{:x}", Sha256::digest(text.as_bytes()))
}

/// Register only real buffers at reviewed capture/read boundaries, never token
/// estimates or reconstructed hypothetical command output.
pub fn register(text: &str, source: &'static str) {
    if !enabled() || (text.is_empty() && source != "git.diff.actual-capture") || text.len() > LIMIT { return; }
    if let Ok(mut raw) = RAW.lock() {
        if raw.len() < 128 { raw.insert(hash(text), source); }
    }
}

pub fn capture(stdout: &str, stderr: &str, source: &'static str) {
    if !enabled() { return; }
    // RTK's streaming raw accumulator caps at 10 MiB. Conservatively exclude
    // large captures so a capped prefix cannot masquerade as the full input.
    if source == "stream.capture" && stdout.len().saturating_add(stderr.len()) >= 8 * 1024 * 1024 { return; }
    register(stdout, source);
    register(stderr, source);
    if stdout.len().saturating_add(stderr.len()).saturating_add(1) <= LIMIT {
        register(&format!("{}{}", stdout, stderr), source);
    }
}

/// The existing tracker supplies the selected before/after representations.
/// Admit before only when it matches a real registered buffer. Python observes
/// final stdout AND stderr, including hints/newlines/diagnostics, independently.
pub fn observe(before: &str, tracked_after: &str) {
    if !enabled() || before.len() > LIMIT || tracked_after.len() > LIMIT { return; }
    let source = RAW.lock().ok().and_then(|raw| raw.get(&hash(before)).copied());
    let Some(source) = source else { return; };
    let Some(Some((path, invocation))) = CONFIG.get() else { return; };
    let event = serde_json::json!({"protocol":"ces-rtk-pair-v1", "pid":std::process::id(),
        "invocation_id":invocation, "source":source, "before":before, "tracked_after":tracked_after});
    let mut options = std::fs::OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
    if let Ok(mut file) = options.open(path) {
        let _ = serde_json::to_writer(&mut file, &event);
        let _ = file.write_all(b"\n");
    }
}
