//! Watching for development tools starting and finishing.
//!
//! The naive version of this — emit an event whenever a matching process
//! appears or disappears — produces almost entirely noise. Editor hooks, shell
//! completions, and language servers spawn short-lived helpers constantly; in
//! practice they outnumbered real events by roughly three hundred to one, and a
//! briefing assembled from that is worse than no briefing.
//!
//! So a process has to *last* before it counts. A `cargo build` runs for
//! seconds; a hook script runs for forty milliseconds. That single rule removes
//! the flood without needing a list of things to ignore, which would never be
//! complete.

use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

use contextd_core::event::{EventSource, RawEvent};
use serde_json::json;
use sysinfo::{Pid, ProcessRefreshKind, RefreshKind, System};
use tokio::sync::broadcast;
use tokio::time::{Duration, interval};
use tracing::{info, warn};
use ulid::Ulid;

/// Targets we actually care about for developer context.
const TARGET_PROCESSES: &[&str] = &["cargo", "node", "npm", "python", "docker", "rustc"];

/// How long a process must survive before it is worth mentioning.
///
/// Everything a developer starts on purpose outlives this. Almost nothing
/// spawned by tooling on their behalf does.
const MIN_LIFETIME_MS: u64 = 2_000;

const POLL_INTERVAL: Duration = Duration::from_secs(1);

/// A process we are watching, and whether we have mentioned it yet.
#[derive(Debug, Clone, PartialEq, Eq)]
struct Tracked {
    name: String,
    command: String,
    first_seen_ms: u64,
    /// Set once a `process_start` has been emitted for this pid.
    announced: bool,
}

/// One process as the poller sees it on a tick.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Seen {
    pub pid: Pid,
    pub name: String,
    pub command: String,
}

/// Something worth telling the rest of the daemon about.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProcEvent {
    Started {
        name: String,
        command: String,
    },
    Finished {
        name: String,
        command: String,
        ran_for_ms: u64,
    },
}

/// The poller's memory, separated from the polling so it can be tested without
/// spawning real processes.
#[derive(Debug, Default)]
pub struct ProcessTracker {
    tracked: HashMap<Pid, Tracked>,
    /// Whether the first poll has happened.
    ///
    /// Everything already running when the daemon starts was not started by
    /// this session, and announcing a docker daemon that has been up for a week
    /// as breaking news is exactly the kind of thing that makes a briefing
    /// untrustworthy.
    seeded: bool,
}

impl ProcessTracker {
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold one poll into the tracker and report what changed.
    pub fn tick(&mut self, seen: Vec<Seen>, now_ms: u64) -> Vec<ProcEvent> {
        let mut events = Vec::new();
        let mut live: Vec<Pid> = Vec::with_capacity(seen.len());
        let seeding = !self.seeded;
        self.seeded = true;

        for process in seen {
            live.push(process.pid);

            let entry = self.tracked.entry(process.pid).or_insert_with(|| Tracked {
                name: process.name,
                command: process.command,
                first_seen_ms: now_ms,
                // Already running before we were watching: remember it, so we
                // can report it finishing, but do not claim it started.
                announced: seeding,
            });

            if !entry.announced && now_ms.saturating_sub(entry.first_seen_ms) >= MIN_LIFETIME_MS {
                entry.announced = true;
                events.push(ProcEvent::Started {
                    name: entry.name.clone(),
                    command: entry.command.clone(),
                });
            }
        }

        // Anything gone since last tick. A process that never lived long enough
        // to be announced is dropped silently — we never claimed it started, so
        // reporting that it finished would be incoherent.
        self.tracked.retain(|pid, entry| {
            if live.contains(pid) {
                return true;
            }
            if entry.announced {
                events.push(ProcEvent::Finished {
                    name: entry.name.clone(),
                    command: entry.command.clone(),
                    ran_for_ms: now_ms.saturating_sub(entry.first_seen_ms),
                });
            }
            false
        });

        events
    }

    /// How many processes are currently being watched.
    pub fn watching(&self) -> usize {
        self.tracked.len()
    }
}

/// True when a process is one we would want to see in a briefing.
fn is_interesting(name: &str, command: &str) -> bool {
    let lowered = name.to_lowercase();
    if !TARGET_PROCESSES
        .iter()
        .any(|target| lowered.contains(target))
    {
        return false;
    }

    // Never report ourselves. The daemon polling for processes and finding
    // itself is the same self-observation problem the filesystem watcher has.
    !command.contains("contextd")
}

pub async fn start_proc_poller(tx: broadcast::Sender<RawEvent>) {
    info!(
        "Starting process poller ({}s interval, {}ms minimum lifetime)",
        POLL_INTERVAL.as_secs(),
        MIN_LIFETIME_MS
    );

    let mut sys = System::new_with_specifics(
        RefreshKind::new().with_processes(ProcessRefreshKind::everything()),
    );
    let mut tracker = ProcessTracker::new();
    let mut ticker = interval(POLL_INTERVAL);

    loop {
        ticker.tick().await;

        // Full process metadata, so command-line arguments stay populated.
        sys.refresh_processes_specifics(ProcessRefreshKind::everything());

        let seen: Vec<Seen> = sys
            .processes()
            .iter()
            .filter_map(|(pid, process)| {
                // On Linux sysinfo reports threads alongside processes, and
                // threads share their parent's command line. Tracking them
                // announced one "started node" per thread — 143 events for a
                // single language server.
                if process.thread_kind().is_some() {
                    return None;
                }

                let name = process.name().to_string();
                let command = process.cmd().join(" ");
                if !is_interesting(&name, &command) {
                    return None;
                }
                Some(Seen {
                    pid: *pid,
                    name,
                    command,
                })
            })
            .collect();

        for event in tracker.tick(seen, now_ms()) {
            emit(&tx, event);
        }
    }
}

fn emit(tx: &broadcast::Sender<RawEvent>, event: ProcEvent) {
    let payload = match event {
        ProcEvent::Started { name, command } => json!({
            "action": "process_start",
            "process_name": name,
            "command": command,
        }),
        ProcEvent::Finished {
            name,
            command,
            ran_for_ms,
        } => json!({
            "action": "process_end",
            "process_name": name,
            "command": command,
            "ran_for_ms": ran_for_ms,
        }),
    };

    let raw = RawEvent {
        id: Ulid::new().to_string(),
        timestamp_ms: now_ms(),
        source: EventSource::Proc,
        payload,
    };

    if let Err(e) = tx.send(raw) {
        warn!("Failed to broadcast process event: {}", e);
    }
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64
}

#[cfg(test)]
mod tests {
    use super::*;

    fn seen(pid: usize, name: &str, command: &str) -> Seen {
        Seen {
            pid: Pid::from(pid),
            name: name.to_string(),
            command: command.to_string(),
        }
    }

    fn cargo(pid: usize) -> Seen {
        seen(pid, "cargo", "cargo build --release")
    }

    /// A tracker that has already done its startup poll, which is the state it
    /// spends all but the first second of its life in.
    fn running() -> ProcessTracker {
        let mut tracker = ProcessTracker::new();
        tracker.tick(vec![], 0);
        tracker
    }

    #[test]
    fn a_process_is_not_announced_the_instant_it_appears() {
        let mut tracker = running();
        assert!(tracker.tick(vec![cargo(1)], 0).is_empty());
    }

    #[test]
    fn a_process_that_lasts_is_announced() {
        let mut tracker = running();
        tracker.tick(vec![cargo(1)], 0);

        let events = tracker.tick(vec![cargo(1)], MIN_LIFETIME_MS);
        assert_eq!(
            events,
            vec![ProcEvent::Started {
                name: "cargo".to_string(),
                command: "cargo build --release".to_string(),
            }]
        );
    }

    #[test]
    fn a_process_is_only_announced_once_however_long_it_runs() {
        let mut tracker = running();
        tracker.tick(vec![cargo(1)], 0);
        tracker.tick(vec![cargo(1)], MIN_LIFETIME_MS);

        for tick in 2..10 {
            assert!(
                tracker
                    .tick(vec![cargo(1)], tick * MIN_LIFETIME_MS)
                    .is_empty(),
                "a long build must not re-announce itself every second"
            );
        }
    }

    #[test]
    fn whatever_was_already_running_at_startup_did_not_just_start() {
        // Otherwise the first briefing after a restart is a list of the docker
        // daemon and every language server, none of which is news.
        let mut tracker = ProcessTracker::new();
        let preexisting = vec![cargo(1), seen(2, "node", "node server.js")];

        assert!(tracker.tick(preexisting.clone(), 0).is_empty());
        assert!(
            tracker
                .tick(preexisting.clone(), MIN_LIFETIME_MS)
                .is_empty()
        );
        assert!(tracker.tick(preexisting, 60_000).is_empty());
    }

    #[test]
    fn something_started_after_boot_is_still_announced() {
        let mut tracker = ProcessTracker::new();
        tracker.tick(vec![seen(2, "node", "node server.js")], 0);

        tracker.tick(vec![seen(2, "node", "node server.js"), cargo(1)], 1_000);
        let events = tracker.tick(
            vec![seen(2, "node", "node server.js"), cargo(1)],
            1_000 + MIN_LIFETIME_MS,
        );

        assert_eq!(events.len(), 1);
        assert!(matches!(&events[0], ProcEvent::Started { name, .. } if name == "cargo"));
    }

    #[test]
    fn a_short_lived_helper_is_never_mentioned_at_all() {
        // This is the whole point. Editor hooks spawn constantly, and reporting
        // them buried real activity roughly three hundred to one.
        let mut tracker = running();
        let hook = seen(7, "python3.12", "python3.12 /plugins/hooks/reminder.py");

        let start = tracker.tick(vec![hook], 0);
        let end = tracker.tick(vec![], 100);

        assert!(start.is_empty());
        assert!(
            end.is_empty(),
            "a process we never announced must not report finishing"
        );
        assert_eq!(tracker.watching(), 0, "and it must not be remembered");
    }

    #[test]
    fn finishing_reports_what_finished_and_for_how_long() {
        // The old poller emitted a bare "process_stop" with no name, because it
        // read the name from a process that no longer existed.
        let mut tracker = running();
        tracker.tick(vec![cargo(1)], 0);
        tracker.tick(vec![cargo(1)], MIN_LIFETIME_MS);

        let events = tracker.tick(vec![], 30_000);
        assert_eq!(
            events,
            vec![ProcEvent::Finished {
                name: "cargo".to_string(),
                command: "cargo build --release".to_string(),
                ran_for_ms: 30_000,
            }]
        );
    }

    #[test]
    fn a_finished_process_is_forgotten() {
        let mut tracker = running();
        tracker.tick(vec![cargo(1)], 0);
        tracker.tick(vec![cargo(1)], MIN_LIFETIME_MS);
        tracker.tick(vec![], 30_000);

        assert_eq!(tracker.watching(), 0);
        assert!(tracker.tick(vec![], 40_000).is_empty());
    }

    #[test]
    fn several_processes_are_tracked_independently() {
        let mut tracker = running();
        let node = seen(2, "node", "node server.js");

        tracker.tick(vec![cargo(1), node.clone()], 0);
        let started = tracker.tick(vec![cargo(1), node], MIN_LIFETIME_MS);
        assert_eq!(started.len(), 2);

        let finished = tracker.tick(vec![cargo(1)], 10_000);
        assert_eq!(finished.len(), 1);
        assert!(matches!(
            &finished[0],
            ProcEvent::Finished { name, .. } if name == "node"
        ));
        assert_eq!(tracker.watching(), 1);
    }

    #[test]
    fn a_reused_pid_starts_a_fresh_clock() {
        let mut tracker = running();
        tracker.tick(vec![cargo(1)], 0);
        tracker.tick(vec![cargo(1)], MIN_LIFETIME_MS);
        tracker.tick(vec![], 10_000);

        // The OS hands pid 1 to something else. It must earn its own lifetime
        // rather than inheriting the previous process's.
        assert!(tracker.tick(vec![cargo(1)], 11_000).is_empty());
    }

    #[test]
    fn only_development_tools_are_watched() {
        assert!(is_interesting("cargo", "cargo build"));
        assert!(is_interesting("node", "node server.js"));
        assert!(!is_interesting("sshd", "sshd -D"));
        assert!(!is_interesting("systemd", "/lib/systemd/systemd"));
    }

    #[test]
    fn the_daemon_does_not_watch_itself() {
        // Otherwise contextd's own cargo builds and test runs become the
        // dominant signal in its own briefings.
        assert!(!is_interesting(
            "contextd",
            "/home/me/contextd/target/debug/contextd"
        ));
        assert!(!is_interesting("cargo", "cargo run -p contextd"));
    }
}
