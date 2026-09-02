#!/usr/bin/env sh
#
# contextd installer.
#
# Builds the daemon, puts it on PATH, and runs `contextd install` to wire up
# the shell hook, git hooks, and the systemd unit.
#
#   curl -fsSL https://raw.githubusercontent.com/vansh5632/contextd/main/install.sh | sh
#
# Written for POSIX sh so it works before anyone has chosen a shell, and with
# `set -e` so a failed step stops rather than reporting a success it did not
# achieve.

set -eu

PREFIX="${PREFIX:-$HOME/.local}"
BIN_DIR="$PREFIX/bin"

say() { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[33m%s\033[0m\n' "$*" >&2; }
die() { printf '\033[31merror: %s\033[0m\n' "$*" >&2; exit 1; }

# --- prerequisites -----------------------------------------------------------

command -v cargo >/dev/null 2>&1 || die \
  "cargo not found. Install Rust from https://rustup.rs and run this again."

# Edition 2024 needs 1.85; rmcp needs 1.88. Checked here rather than letting
# cargo fail three minutes into a build with a message about editions.
rust_version=$(rustc --version | cut -d' ' -f2)
rust_minor=$(printf '%s' "$rust_version" | cut -d. -f2)
if [ "$rust_minor" -lt 88 ]; then
  die "Rust $rust_version is too old; contextd needs 1.88 or newer. Run: rustup update"
fi

command -v nc >/dev/null 2>&1 || warn \
  "nc (netcat) not found. The shell and git hooks use it to report events;
   install it (apt install netcat-openbsd) or those hooks will do nothing."

if ! command -v ollama >/dev/null 2>&1; then
  warn "ollama not found. contextd works without it, but semantic search
   and 'last time I hit this error' need embeddings. See https://ollama.com"
fi

# --- build -------------------------------------------------------------------

say "Building contextd (this takes a few minutes the first time)..."
cargo build --release --locked

binary="target/release/contextd"
[ -x "$binary" ] || die "build finished but $binary is missing"

# --- install -----------------------------------------------------------------

say "Installing to $BIN_DIR"
mkdir -p "$BIN_DIR"
install -m 755 "$binary" "$BIN_DIR/contextd"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this to your shell profile:
   export PATH=\"\$PATH:$BIN_DIR\"" ;;
esac

# --- wire it up --------------------------------------------------------------

say "Setting up hooks and service..."
"$BIN_DIR/contextd" install

# --- start it ----------------------------------------------------------------

if command -v systemctl >/dev/null 2>&1 && [ -d "$HOME/.config/systemd/user" ]; then
  systemctl --user daemon-reload 2>/dev/null || true
  if systemctl --user enable --now contextd 2>/dev/null; then
    say "contextd is running."
  else
    warn "Could not start the service automatically. Start it yourself with:
   systemctl --user enable --now contextd"
  fi
else
  warn "No systemd user session. Start the daemon manually with: contextd run"
fi

cat <<'EOF'

Next: point your agent at contextd.

  Cursor        ~/.cursor/mcp.json
  Claude Code   ~/.claude.json, or .mcp.json in a project

    {
      "mcpServers": {
        "contextd": { "command": "contextd", "args": ["mcp"] }
      }
    }

Then open a new terminal and run `contextd status` to check on it.
EOF
