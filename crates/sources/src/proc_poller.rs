use contextd_core::event::{EventSource, RawEvent};
use serde_json::json;
use std::collections::HashSet;
use std::time::{SystemTime, UNIX_EPOCH};
use sysinfo::{Pid, ProcessRefreshKind, RefreshKind, System};
use tokio::sync::broadcast;
use tokio::time::{interval, Duration};
use tracing::{info, warn};
use ulid::Ulid;

/// Targets we actually care about for developer context
const TARGET_PROCESSES: &[&str] = &["cargo", "node", "npm", "python", "docker", "rustc"];

pub async fn start_proc_poller(tx: broadcast::Sender<RawEvent>) {
    info!("Starting process poller (10s interval)");

    // Initialize system information, specifically only asking for process data to save CPU
    let mut sys = System::new_with_specifics(
        RefreshKind::new().with_processes(ProcessRefreshKind::everything()),
    );

    let mut known_pids: HashSet<Pid> = HashSet::new();
    let mut ticker = interval(Duration::from_secs(10));

    loop {
        // Wait for the next 10-second tick
        ticker.tick().await;

        // Refresh with full process metadata so command-line arguments stay populated.
        sys.refresh_processes_specifics(ProcessRefreshKind::everything());

        let mut current_pids: HashSet<Pid> = HashSet::new();

        for (pid, process) in sys.processes() {
            let name = process.name().to_lowercase();

            // Is this a developer tool we care about?
            if TARGET_PROCESSES.iter().any(|&t| name.contains(t)) {
                current_pids.insert(*pid);

                // If it's in current but wasn't in known, it just started!
                if !known_pids.contains(pid) {
                    emit_proc_event(&tx, "process_start", name, process.cmd().join(" "));
                }
            }
        }

        // If it was in known but isn't in current, it just stopped!
        for old_pid in &known_pids {
            if !current_pids.contains(old_pid) {
                // sysinfo removes dead processes from its map, so we can't get the name easily here
                // For a v1, emitting a generic stop is fine. We can cache names later if needed.
                emit_proc_event(&tx, "process_stop", "unknown".to_string(), "".to_string());
            }
        }

        // Update our state for the next tick
        known_pids = current_pids;
    }
}

fn emit_proc_event(tx: &broadcast::Sender<RawEvent>, action: &str, name: String, cmd: String) {
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis() as u64;

    let event = RawEvent {
        id: Ulid::new().to_string(),
        timestamp_ms,
        source: EventSource::Proc,
        payload: json!({
            "action": action,
            "process_name": name,
            "command": cmd
        }),
    };

    if let Err(e) = tx.send(event) {
        warn!("Failed to broadcast process event: {}", e);
    }
}
