import importlib.util
import pathlib
import subprocess
import sys

import pytest

import httpcore

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

RESPONSE = [
    b"HTTP/1.1 200 OK\r\n",
    b"Content-Type: plain/text\r\n",
    b"Content-Length: 13\r\n",
    b"\r\n",
    b"Hello, world!",
]

CAP_ORIGINS = [
    "https://a.example.com/",
    "https://b.example.com/",
    "https://c.example.com/",
    "https://d.example.com/",
]


def test_cap_sync_max_three_four_origins():
    network_backend = httpcore.MockBackend(RESPONSE)
    with httpcore.ConnectionPool(
        max_connections=3, network_backend=network_backend
    ) as pool:
        for url in CAP_ORIGINS[:-1]:
            pool.request("GET", url)
        with pool.stream("GET", CAP_ORIGINS[-1]) as response:
            n = len(pool.connections)
            response.read()
        assert n == 3
        assert len(pool.connections) <= 3


@pytest.mark.anyio
async def test_cap_async_max_three_four_origins():
    network_backend = httpcore.AsyncMockBackend(RESPONSE)
    async with httpcore.AsyncConnectionPool(
        max_connections=3, network_backend=network_backend
    ) as pool:
        for url in CAP_ORIGINS[:-1]:
            await pool.request("GET", url)
        async with pool.stream("GET", CAP_ORIGINS[-1]) as response:
            n = len(pool.connections)
            await response.aread()
        assert n == 3
        assert len(pool.connections) <= 3


KEEPALIVE_DURING_C = [
    "<HTTPConnection ['https://b.example.com:443', HTTP/1.1, ACTIVE, Request Count: 1]>",
    "<HTTPConnection ['https://c.example.com:443', HTTP/1.1, ACTIVE, Request Count: 1]>",
]
KEEPALIVE_AFTER_C = [
    "<HTTPConnection ['https://b.example.com:443', HTTP/1.1, ACTIVE, Request Count: 1]>",
]
KEEPALIVE_END = [
    "<HTTPConnection ['https://b.example.com:443', HTTP/1.1, IDLE, Request Count: 1]>",
]


def _async_reprs(expected):
    return [line.replace("<HTTPConnection", "<AsyncHTTPConnection") for line in expected]


def test_keepalive_cleanup_unchanged_sync():
    network_backend = httpcore.MockBackend(RESPONSE)
    with httpcore.ConnectionPool(
        max_connections=4,
        max_keepalive_connections=1,
        network_backend=network_backend,
    ) as pool:
        pool.request("GET", "https://a.example.com/")
        with pool.stream("GET", "https://b.example.com/") as outer:
            with pool.stream("GET", "https://c.example.com/") as inner:
                assert [repr(c) for c in pool.connections] == KEEPALIVE_DURING_C
                inner.read()
            assert [repr(c) for c in pool.connections] == KEEPALIVE_AFTER_C
            outer.read()
        assert [repr(c) for c in pool.connections] == KEEPALIVE_END


@pytest.mark.anyio
async def test_keepalive_cleanup_unchanged_async():
    network_backend = httpcore.AsyncMockBackend(RESPONSE)
    async with httpcore.AsyncConnectionPool(
        max_connections=4,
        max_keepalive_connections=1,
        network_backend=network_backend,
    ) as pool:
        await pool.request("GET", "https://a.example.com/")
        async with pool.stream("GET", "https://b.example.com/") as outer:
            async with pool.stream("GET", "https://c.example.com/") as inner:
                assert [repr(c) for c in pool.connections] == _async_reprs(
                    KEEPALIVE_DURING_C
                )
                await inner.aread()
            assert [repr(c) for c in pool.connections] == _async_reprs(
                KEEPALIVE_AFTER_C
            )
            await outer.aread()
        assert [repr(c) for c in pool.connections] == _async_reprs(KEEPALIVE_END)


def _load_unasync():
    spec = importlib.util.spec_from_file_location(
        "_hidden_unasync", REPO_ROOT / "scripts" / "unasync.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unasync_script_check_passes():
    result = subprocess.run(
        [sys.executable, "scripts/unasync.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "async_dir,sync_dir",
    [("httpcore/_async", "httpcore/_sync"), ("tests/_async", "tests/_sync")],
)
def test_sync_tree_matches_regenerated(async_dir, sync_dir):
    # The upstream --check zips lines, so it misses length differences.
    unasync = _load_unasync()
    for in_path in sorted((REPO_ROOT / async_dir).glob("*.py")):
        out_path = REPO_ROOT / sync_dir / in_path.name
        with open(in_path) as in_file:
            expected = "".join(unasync.unasync_line(line) for line in in_file)
        with open(out_path, newline="") as out_file:
            actual = out_file.read()
        assert actual == expected, f"{out_path.relative_to(REPO_ROOT)} is stale"
