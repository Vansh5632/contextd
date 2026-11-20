import inspect

from courier import Courier


def test_a_later_successful_send_does_not_add_another_retry():
    courier = Courier()
    courier.deliver(b"ping")
    courier.deliver(b"pong")
    assert courier.retry_count == 1


def test_deliver_takes_only_the_message():
    assert list(inspect.signature(Courier.deliver).parameters) == ["self", "message"]
