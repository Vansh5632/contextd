from courier import Courier


def test_failed_send_counts_as_one_retry():
    courier = Courier()
    courier.deliver(b"ping")
    assert courier.retry_count == 1, f"retry_count {courier.retry_count} != 1"
