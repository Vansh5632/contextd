"""Isolated contextd process and Unix-socket client.

Errors from the daemon, including ``query handler unavailable``, are data.
They are not raised.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]


class DaemonError(RuntimeError):
    pass


def binary_path() -> Path:
    return REPO_ROOT / "target" / "debug" / "contextd"


def ensure_binary(*, rebuild: bool = False) -> Path:
    path = binary_path()
    if path.exists() and not rebuild:
        return path
    result = subprocess.run(
        ["cargo", "build", "-p", "contextd", "--bin", "contextd"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 or not path.exists():
        raise DaemonError((result.stderr or "")[-4000:] + (result.stdout or "")[-1000:])
    return path


class IsolatedDaemon:
    """One daemon, one temporary data directory, no repo git hooks."""

    def __init__(self, binary: Path) -> None:
        self.binary = binary
        self.root = Path(tempfile.mkdtemp(prefix="c21eval-", dir="/tmp"))
        self.socket_path = self.root / "runtime" / "contextd.sock"
        self.db_path = self.root / "data" / "events.db"
        self._proc: subprocess.Popen[str] | None = None
        self._stderr: list[str] = []
        self._reader: threading.Thread | None = None

    def start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            raise DaemonError("daemon is already running")
        config_dir = self.root / "config" / "contextd"
        for path in (config_dir, self.root / "data", self.root / "runtime", self.root / "watch"):
            path.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            self.socket_path.unlink()
        (config_dir / "config.toml").write_text(
            "\n".join(
                [
                    f'db_path = "{self.db_path}"',
                    f'socket_path = "{self.socket_path}"',
                    f'watch_root = "{self.root / "watch"}"',
                    'ollama_url = "http://127.0.0.1:9"',
                    'embedding_model = "nomic-embed-text"',
                    "max_memory_mb = 50",
                    "",
                ]
            )
        )
        env = os.environ.copy()
        env["XDG_CONFIG_HOME"] = str(self.root / "config")
        env["XDG_DATA_HOME"] = str(self.root / "data")
        env["XDG_RUNTIME_DIR"] = str(self.root / "runtime")
        env["RUST_LOG"] = "info"
        env["RUST_BACKTRACE"] = "1"
        self._proc = subprocess.Popen(
            [str(self.binary), "run"],
            cwd=self.root / "watch",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        assert self._proc.stderr is not None
        self._reader = threading.Thread(
            target=self._drain,
            args=(self._proc.stderr,),
            daemon=True,
        )
        self._reader.start()
        self._wait_until_ready()

    def restart(self) -> None:
        self.stop(remove_root=False)
        self.start()

    def stop(self, remove_root: bool = False) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    proc.kill()
                proc.wait(timeout=5)
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None
        if remove_root:
            shutil.rmtree(self.root, ignore_errors=True)

    def close(self) -> None:
        self.stop(remove_root=True)

    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stderr_text(self) -> str:
        return "".join(self._stderr)

    def ask(self, request: dict[str, Any], timeout: float = 20) -> dict[str, Any]:
        """Send one JSON line. Transport failures and daemon error objects are fields."""
        outcome: dict[str, Any] = {
            "request_query": request.get("query"),
            "response": None,
            "error": None,
            "transport_error": None,
        }
        if not self.alive():
            outcome["transport_error"] = "daemon process is not running"
            outcome["error"] = outcome["transport_error"]
            return outcome
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(self.socket_path))
            payload = json.dumps(request, separators=(",", ":")) + "\n"
            client.sendall(payload.encode())
            client.shutdown(socket.SHUT_WR)
            chunks: list[bytes] = []
            while True:
                piece = client.recv(65536)
                if not piece:
                    break
                chunks.append(piece)
            body = b"".join(chunks).decode()
        except (OSError, socket.timeout) as exc:
            outcome["transport_error"] = str(exc)
            outcome["error"] = str(exc)
            return outcome
        finally:
            client.close()
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            outcome["transport_error"] = f"daemon reply was not JSON: {body[:300]}"
            outcome["error"] = outcome["transport_error"]
            return outcome
        if not isinstance(parsed, dict):
            outcome["transport_error"] = f"daemon reply was not an object: {body[:300]}"
            outcome["error"] = outcome["transport_error"]
            return outcome
        outcome["response"] = parsed
        if parsed.get("error"):
            outcome["error"] = str(parsed["error"])
        return outcome

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + 20
        last_error = "daemon socket did not answer"
        while time.monotonic() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise DaemonError(
                    f"daemon exited {self._proc.returncode} before the socket answered\n"
                    + self.stderr_text()[-2000:]
                )
            if self.socket_path.exists():
                outcome = self.ask({"query": "now"}, timeout=5)
                if outcome["transport_error"] is None and outcome["response"] is not None:
                    return
                last_error = outcome["error"] or last_error
            time.sleep(0.05)
        raise DaemonError(last_error + "\n" + self.stderr_text()[-2000:])

    def _drain(self, pipe: Any) -> None:
        try:
            for line in pipe:
                self._stderr.append(line)
        except ValueError:
            return
