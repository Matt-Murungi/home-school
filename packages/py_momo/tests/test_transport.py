import http.server
import threading
import time

import pytest

from py_momo import HttpRequest, UrllibTransport
from py_momo.errors import TransportError, TransportTimeout


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/slow":
            time.sleep(1.0)
        code = 404 if self.path == "/missing" else 200
        self.send_response(code)
        self.end_headers()
        self.wfile.write(b"hi")

    def log_message(self, *a):
        pass


@pytest.fixture
def server():
    s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{s.server_port}"
    s.shutdown()


def test_urllib_transport_returns_any_status(server):
    t = UrllibTransport()
    assert t.send(HttpRequest("GET", server + "/ok")).status == 200
    assert t.send(HttpRequest("GET", server + "/missing")).status == 404


def test_urllib_transport_timeout(server):
    with pytest.raises(TransportTimeout):
        UrllibTransport().send(HttpRequest("GET", server + "/slow", timeout=0.2))


def test_urllib_transport_connection_refused():
    with pytest.raises(TransportError):
        UrllibTransport().send(HttpRequest("GET", "http://127.0.0.1:1/", timeout=1))
