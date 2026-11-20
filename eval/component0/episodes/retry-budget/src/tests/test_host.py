from host import format_host


def test_non_default_port_is_kept():
    assert format_host("example.com", 80, 443) == "example.com:80"
    assert format_host("example.com", 443, 443) == "example.com"
