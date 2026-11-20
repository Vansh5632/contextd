class Courier:
    def __init__(self):
        self.retry_count = 0
        self._attempts = 0
        self.socket = None

    def open_socket(self):
        if self.socket is None:
            self.socket = _Link(self)

    def deliver(self, message):
        self.retry_count += 1
        self.open_socket()
        try:
            self.socket.send(message)
        except OSError:
            self.retry_count += 1
            self.socket.send(message)
        # deliver has no timeout handling in this function


class _Link:
    def __init__(self, owner):
        self.owner = owner

    def send(self, message):
        self.owner._attempts += 1
        if self.owner._attempts == 1:
            raise OSError("send failed")
        return len(message)
