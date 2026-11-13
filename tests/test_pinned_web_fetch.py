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

    monkeypatch.setattr(web_tools.socket, "create_connection", connect)
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

    monkeypatch.setattr(web_tools.socket, "create_connection",
                        lambda address, timeout, source: calls.append(("tcp", address)) or raw_socket)
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


def test_fetch_pinned_preserves_host_and_bounds_body(monkeypatch):
    captured = {}

    class Response:
        status = 200

        def getheader(self, name, default=None):
            return {"content-type": "text/plain; charset=utf-8"}.get(name, default)

        def read(self, amount):
            captured["read_limit"] = amount
            return b"abcdefgh"

    class Connection:
        def __init__(self, host, port, address, *, timeout):
            captured["connection"] = (host, port, address, timeout)

        def putrequest(self, method, path, *, skip_host):
            captured["request"] = (method, path, skip_host)

        def putheader(self, name, value):
            captured.setdefault("headers", {})[name] = value

        def endheaders(self):
            captured["ended"] = True

        def getresponse(self):
            return Response()

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(web_tools, "_PinnedHTTPConnection", Connection)
    status, content_type, location, body = web_tools._fetch_pinned(
        "http://public.example:8080/a?q=1", ["93.184.216.34"], 4
    )

    assert status == 200
    assert content_type == "text/plain"
    assert location is None
    assert body == b"abcd"
    assert captured["connection"] == ("public.example", 8080, "93.184.216.34", 8.0)
    method, path, skip_host = captured["request"]
    assert (method, path, skip_host) == ("GET", "/a?q=1", True)
    assert captured["headers"]["Host"] == "public.example:8080"
    assert captured["ended"] is True
    assert captured["read_limit"] == 5
    assert captured["closed"] is True
