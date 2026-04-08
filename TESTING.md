# Testing Setup

This workspace uses Rust's built-in test runner through `cargo test`. Tests currently live in three places:

- Unit tests inside source files with `#[cfg(test)]` modules, such as [crates/core/src/event.rs](crates/core/src/event.rs) and [crates/sources/src/shell.rs](crates/sources/src/shell.rs).
- Shared test helpers in [crates/core/src/test_utils.rs](crates/core/src/test_utils.rs).
- Cross-module integration tests in [crates/store/tests/integration.rs](crates/store/tests/integration.rs).

## Workspace Layout

- `contextd-core` contains the core data types and small deterministic tests around serialization and config behavior.
- `sources` contains async event-ingestion code. Its tests use `#[tokio::test]` because the shell listener runs on Tokio and communicates over Unix domain sockets.
- `store` contains database initialization and persistence tests.

## How Tests Are Run

Run the full workspace:

```bash
cargo test
```

Run one crate:

```bash
cargo test -p sources
```

Run one specific test:

```bash
cargo test -p sources shell_listener_receives_and_broadcasts_events
```

## Current Test Conventions

- Prefer small unit tests close to the code they verify.
- Use integration tests when behavior crosses crate or storage boundaries.
- Reuse helpers from `contextd-core::test_utils` for canonical configs and events when possible.
- Use temporary files or directories for filesystem and socket tests so runs stay isolated.
- Async tests use Tokio.

## Shell Listener Test

The shell listener test in [crates/sources/src/shell.rs](/home/vansh5632/contextd/crates/sources/src/shell.rs) validates the real listener flow:

1. Create a temporary Unix socket path.
2. Start `start_shell_listener` in a background Tokio task.
3. Connect with `tokio::net::UnixStream`.
4. Send one newline-delimited JSON `RawEvent`.
5. Assert the event is broadcast back through `tokio::sync::broadcast`.

This matches the production contract in the current codebase: the listener expects newline-delimited JSON and forwards successfully parsed `RawEvent` values to the rest of the application.
