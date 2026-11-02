import httpcore


def test_pool_counts():
    network_backend = httpcore.MockBackend(
        [
            b"HTTP/1.1 200 OK\r\n",
            b"Content-Type: plain/text\r\n",
            b"Content-Length: 13\r\n",
            b"\r\n",
            b"Hello, world!",
        ]
    )

    with httpcore.ConnectionPool(
        max_connections=2, network_backend=network_backend
    ) as pool:
        pool.request("GET", "https://a.example.com/")
        pool.request("GET", "https://b.example.com/")
        with pool.stream("GET", "https://c.example.com/") as response:
            n = len(pool.connections)
            response.read()

    assert n <= 2, f"count {n} diverged from 2"
