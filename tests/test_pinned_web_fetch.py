from __future__ import annotations

from matrix_ai.tools import web_tools


class _Socket:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_http_connection_uses_only_the_validated_numeric_address(monkeypatch):
    calls = []
    sock = _Socket()

    def connect(address, timeout, source_address):
        calls.append((address, timeout, source_address))
        return sock

    monkeypatch.setattr(web_tools._PinnedHTTPConnection, "_create_connection", staticmethod(connect))
    connection = web_tools._PinnedHTTPConnection(
        "public.example", 443, "93.184.216.34", timeout=8.0
    )
    connection.connect()

    assert calls == [(("93.184.216.34", 443), 8.0, None)]
    assert connection.sock is sock
    connection.close()


def test_https_connection_pins_ip_but_keeps_hostname_for_tls_sni(monkeypatch):
    calls = []
    raw_socket = _Socket()

    class TLSSocket(_Socket):
        pass

    tls_socket = TLSSocket()

    class Context:
        def wrap_socket(self, sock, *, server_hostname):
            calls.append(("sni", server_hostname))
            return tls_socket

    monkeypatch.setattr(web_tools._PinnedHTTPSConnection, "_create_connection",
                        staticmethod(lambda address, timeout, source: calls.append(("tcp", address)) or raw_socket))
    monkeypatch.setattr(web_tools.ssl, "create_default_context", lambda: Context())

    connection = web_tools._PinnedHTTPSConnection(
        "public.example", 443, "93.184.216.34", timeout=8.0
    )
    connection.connect()

    assert calls == [
        ("tcp", ("93.184.216.34", 443)),
        ("sni", "public.example"),
    ]
    assert connection.sock is tls_socket
    connection.close()
    assert tls_socket.closed
