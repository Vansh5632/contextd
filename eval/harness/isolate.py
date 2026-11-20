"""One eval run: temp XDG dirs, a private daemon, never the developer's DB."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO

from .unix import ask


@dataclass
class IsolatedDaemon:
    root: Path
    env: dict[str, str]
    socket_path: Path
    db_path: Path
    watch_root: Path
    proc: subprocess.Popen[bytes]
    binary: Path
    log_file: IO[bytes]

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        try:
            self.log_file.close()
        except Exception:
            pass
        shutil.rmtree(self.root, ignore_errors=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def find_binary() -> Path:
    override = os.environ.get("CONTEXTD_BIN")
    if override:
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"CONTEXTD_BIN is not a file: {path}")
        return path
    debug = repo_root() / "target" / "debug" / "contextd"
    if debug.is_file():
        return debug
    release = repo_root() / "target" / "release" / "contextd"
    if release.is_file():
        return release
    which = shutil.which("contextd")
    if which:
        return Path(which)
    raise FileNotFoundError(
        "contextd binary not found. Build with `cargo build -p contextd` "
        "or set CONTEXTD_BIN."
    )


def start_daemon(binary: Path | None = None) -> IsolatedDaemon:
    binary = binary or find_binary()
    root = Path(tempfile.mkdtemp(prefix="contextd-eval-"))
    config_home = root / "config"
    data_home = root / "data"
    runtime = root / "run"
    watch_root = root / "watch"
    for path in (
        config_home / "contextd",
        data_home / "contextd",
        runtime / "contextd",
        watch_root,
    ):
        path.mkdir(parents=True, exist_ok=True)

    db_path = data_home / "contextd" / "events.db"
    socket_path = runtime / "contextd" / "contextd.sock"
    config_path = config_home / "contextd" / "config.toml"
    config_path.write_text(
        "\n".join(
            [
                f'db_path = "{db_path}"',
                f'socket_path = "{socket_path}"',
                f'watch_root = "{watch_root}"',
                'ollama_url = "http://127.0.0.1:1"',
                'embedding_model = "nomic-embed-text"',
                "",
            ]
        )
    )

    env = os.environ.copy()
    env.pop("OLLAMA_HOST", None)
    env["XDG_CONFIG_HOME"] = str(config_home)
    env["XDG_DATA_HOME"] = str(data_home)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["RUST_LOG"] = env.get("RUST_LOG", "warn")

    log_path = root / "daemon.log"
    log_file = log_path.open("wb")
    proc = subprocess.Popen(
        [str(binary), "run"],
        cwd=str(watch_root),
        env=env,
        stdout=log_file,
        stderr=log_file,
    )
    daemon = IsolatedDaemon(
        root=root,
        env=env,
        socket_path=socket_path,
        db_path=db_path,
        watch_root=watch_root,
        proc=proc,
        binary=binary,
        log_file=log_file,
    )
    try:
        _wait_until_ready(daemon)
    except Exception:
        log_file.close()
        tail = ""
        if log_path.exists():
            tail = log_path.read_text(errors="replace")[-2000:]
        daemon.close()
        raise RuntimeError(f"daemon failed to start: {tail}") from None
    return daemon


def _wait_until_ready(daemon: IsolatedDaemon, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    last_error = "socket never appeared"
    while time.time() < deadline:
        if daemon.proc.poll() is not None:
            raise RuntimeError(f"daemon exited with {daemon.proc.returncode}")
        if daemon.socket_path.exists():
            try:
                ask(daemon.socket_path, {"query": "now"})
                return
            except Exception as err:
                last_error = str(err)
        time.sleep(0.05)
    raise TimeoutError(last_error)
