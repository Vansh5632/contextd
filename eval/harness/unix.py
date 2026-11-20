"""Unix-socket client. Matches mcp::daemon: write a line, half-close, maybe read."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any


def send_line(socket_path: Path, payload: dict[str, Any], *, expect_reply: bool) -> str | None:
    raw = json.dumps(payload, separators=(",", ":")) + "\n"
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.settimeout(10)
        sock.connect(str(socket_path))
        sock.sendall(raw.encode())
        sock.shutdown(socket.SHUT_WR)
        if not expect_reply:
            return None
        chunks: list[bytes] = []
        while True:
            piece = sock.recv(65_536)
            if not piece:
                break
            chunks.append(piece)
            if b"\n" in piece:
                break
        reply = b"".join(chunks).decode().strip()
        if not reply:
            raise RuntimeError("daemon closed the socket without replying")
        return reply
    finally:
        sock.close()


def inject_event(socket_path: Path, event: dict[str, Any]) -> None:
    if "query" in event:
        raise ValueError("ingest events must not carry a query key")
    send_line(socket_path, event, expect_reply=False)


def ask(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    reply = send_line(socket_path, request, expect_reply=True)
    assert reply is not None
    return json.loads(reply)
