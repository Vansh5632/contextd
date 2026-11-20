# Build and setup

Current setup guide, checked against the working tree on **10 November 2026**. For the crate map and data flow, use [architecture](architecture.md); for unfinished work, use [status](status.md).

## Prerequisites

- Linux and a native C/linker build toolchain.
- Rust/Cargo **1.88 or newer**, as declared by the workspace.
- Git; `nc` (netcat) for installed shell/Git hook delivery. OpenBSD netcat is the installer's suggested Linux package.
- Optional: a systemd user session for background startup; Ollama with a 768-dimensional embedding model for semantic search.

SQLite is bundled through `rusqlite`; a separate SQLite server is not needed. The normal release build is a native executable, not a verified static binary.

## Build without installing

From the repository root:

```bash
cargo build --locked
./target/debug/contextd help
./target/debug/contextd status
```

Choose the project to watch before starting the daemon:

```bash
cd /path/to/project
/path/to/contextd/target/debug/contextd run
```

`watch_root` defaults to the startup directory. The foreground daemon does not install shell-profile hooks, but it does install managed Git hooks if the watched root is inside a repository. Use a disposable project for experiments. Stop the foreground process with Ctrl-C.

Use the same absolute binary path from another terminal for commands if you have not installed it.

## Install integrations

From the checkout:

```bash
./install.sh
```

The script:

1. Builds with `cargo build --release --locked`.
2. Installs `contextd` to `${PREFIX:-$HOME/.local}/bin`.
3. Runs `contextd install` to create config (if absent), add a managed shell block, write a systemd user unit, and install Git hooks in the current repository when present.
4. Attempts `systemctl --user daemon-reload` and `systemctl --user enable --now contextd`; failure to start the user service is reported and a foreground run is still available.

Ensure the install directory is on PATH. Open a new terminal to load the hook, or source the shell profile named by the installer. Bash, Zsh, and Fish are supported by the generated snippets. Hooks need `nc`; missing `nc` means no hook delivery.

Installation inside the contextd checkout makes that checkout the default service working directory. To watch another project, set an absolute `watch_root` in the config. After changing it, restart the service. If you run `contextd install` again, it also refreshes the unit working directory and installs hooks in the current repository.

## Configuration and paths

```bash
contextd status
```

This prints the effective config, database, and socket paths and probes the daemon. Defaults:

| Setting | Default |
|---|---|
| Config file | `$XDG_CONFIG_HOME/contextd/config.toml`, otherwise `~/.config/contextd/config.toml` |
| Database | `$XDG_DATA_HOME/contextd/events.db`, otherwise `~/.local/share/contextd/events.db` |
| Socket | `$XDG_RUNTIME_DIR/contextd/contextd.sock`, otherwise the contextd data directory |
| `watch_root` | Daemon startup directory |
| `ollama_url` | `http://localhost:11434` |
| `embedding_model` | `nomic-embed-text` |
| `max_memory_mb` | `500`, **not enforced** |

Every TOML field is optional. A minimal example:

```toml
watch_root = "/home/you/projects/thing"
ollama_url = "http://localhost:11434"
embedding_model = "nomic-embed-text"
```

`db_path` and `socket_path` also accept absolute paths. Relative `watch_root` is resolved against the daemon's working directory. `contextd install` preserves an existing config file. Currently the CLI logs malformed/unreadable config and falls back to defaults; check `status` and stderr when a setting appears ignored.

For optional semantic search, run your Ollama service and pull the default model:

```bash
ollama pull nomic-embed-text
```

The client uses `/api/embed` and requires exactly 768 dimensions. `CONTEXTD_EMBEDDING_MODEL` overrides the model when supplied in the daemon environment. Changing the embedding model does not migrate existing vectors; do not assume vectors from different models are comparable. Change the endpoint through `ollama_url`: `OLLAMA_HOST` does not override the URL passed by the daemon's config loader.

Without Ollama, ingestion, rule-based summaries, extraction, and current briefings continue; search and recall fall back to literal payload matching. A configured remote Ollama endpoint receives embedding input.

## Background service

After `contextd install`:

```bash
systemctl --user daemon-reload
systemctl --user enable --now contextd
systemctl --user status contextd
journalctl --user -u contextd -n 50 --no-pager
```

After editing configuration:

```bash
systemctl --user restart contextd
contextd status
```

Stop an existing service before starting a foreground daemon against its database/socket. Run one daemon per database/socket pair. Starting another daemon against the same socket can replace its socket file; it is not an instance-management command.

## Smoke check

With the daemon running and the installed binary on PATH:

```bash
contextd intent "investigate a failing check"
contextd claim constraint "keep the public API unchanged"
contextd observe '{"kind":"tool","session":"smoke","command":"example-check","cwd":"/tmp/contextd-smoke","exit_code":1,"output":"example check failed"}'
contextd handoff
contextd status
```

These commands record illustrative activity; they do not execute `example-check`. Use a disposable configuration if you do not want synthetic activity in your real context. To inspect the JSON briefing, use the socket example in [connecting agents](connecting-agents.md#direct-socket-access), or call MCP `context_now` from your client.

The observation response contains `stored` and `event_id`. Handoff returns `accepted`, `rejected`, and `skipped` lists. A raw note is not guaranteed to yield a claim. For a real grounded conclusion, follow the `observe` example in [connecting agents](connecting-agents.md#record-tool-output-for-handoffs).

## Uninstall

First stop and disable the user service, then remove the managed integrations:

```bash
systemctl --user disable --now contextd
contextd uninstall
systemctl --user daemon-reload
```

If you never installed a systemd unit, skip the systemctl commands. `uninstall` removes the managed shell block from the selected profile and deletes the unit file. **It does not stop an already-running daemon or remove Git hooks, the executable, configuration, or recorded data.** Existing terminals may retain loaded hook functions until restarted.

Git hooks may wrap preserved pre-existing hooks; inspect the managed hook and its backup before restoring anything. Keep the database/config unless you explicitly want to discard recorded context. This guide does not prescribe automatic deletion of those files.

## Development checks

Use [TESTING.md](../TESTING.md) for Rust and Python checks. Installing or starting the daemon is not required for unit tests, and live-agent matrices are separate from local verification.
