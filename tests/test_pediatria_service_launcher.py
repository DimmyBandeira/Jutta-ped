from __future__ import annotations

import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.jutta_ped.service import launcher


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_is_service_running_false_when_nothing_listening() -> None:
    port = _free_port()
    assert launcher.is_service_running(port=port, timeout=0.2) is False


def test_is_service_running_true_when_health_endpoint_responds() -> None:
    port = _free_port()
    server = HTTPServer(("127.0.0.1", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert launcher.is_service_running(port=port, timeout=1.0) is True
    finally:
        server.shutdown()
        thread.join(timeout=2.0)


def test_build_start_command_uses_current_interpreter_and_service_script() -> None:
    command = launcher.build_start_command(host="127.0.0.1", port=9000)
    assert command[0] == sys.executable
    assert command[1] == str(launcher.SERVICE_SCRIPT)
    assert "--host" in command and "127.0.0.1" in command
    assert "--port" in command and "9000" in command
