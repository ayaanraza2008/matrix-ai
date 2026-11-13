from __future__ import annotations

import io
import socket
from unittest.mock import Mock

import pytest

from matrix_ai.errors import PermissionDenied
from matrix_ai.tools import web_tools


class FakeSocket:
    def __init__(self, peer: str):
        self.peer = peer
        self.connected_to = None
        self.sent = b""
        self.closed = False

    def settimeout(self, value):
        self.timeout = value

    def connect(self, target):
        self.connected_to = target

    def getpeername(self):
        return (self.peer, 443 if len(self.connected_to) == 4 else 80)

    def sendall(self, data):
        self.sent += data

    def makefile(self, mode):
        return io.BytesIO(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/plain\r\n"
            b"Content-Length: 5\r\n"
            b"Connection: close\r\n\r\n"
            b"hello"
        )

    def close(self):
        self.closed = True


def test_resolver_rejects_private_and_returns_all_public_answers():
    infos = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:4860:4860::8888", 443, 0, 0)),
    ]
    assert web_tools.resolve_and_validate("https://example.com", lambda *a, **k: infos) == [
        "8.8.8.8",
        "2001:4860:4860::8888",
    ]
    private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
    with pytest.raises(PermissionDenied):
        web_tools.resolve_and_validate("https://example.com", lambda *a, **k: private)


@pytest.mark.parametrize(
    ("address", "family", "expected_target"),
    [
        ("8.8.8.8", socket.AF_INET, ("8.8.8.8", 443)),
        ("2001:4860:4860::8888", socket.AF_INET6, ("2001:4860:4860::8888", 443, 0, 0)),
    ],
)
def test_fetch_connects_to_numeric_validated_ipv4_or_ipv6(monkeypatch, address, family, expected_target):
    fake = FakeSocket(address)
    socket_factory = Mock(return_value=fake)
    monkeypatch.setattr(web_tools.socket, "socket", socket_factory)
    status, headers, body = web_tools._fetch_from_pinned_address(
        "https://example.com/path?q=1", address
    )
    assert status == 200
    assert headers["content-type"] == "text/plain"
    assert body == b"hello"
    socket_factory.assert_called_once_with(family, socket.SOCK_STREAM)
    assert fake.connected_to == expected_target
    assert b"Host: example.com" in fake.sent
    assert b"GET /path?q=1 HTTP/1.1" in fake.sent


def test_https_preserves_hostname_for_sni_and_certificate_verification(monkeypatch):
    fake = FakeSocket("8.8.8.8")
    monkeypatch.setattr(web_tools.socket, "socket", Mock(return_value=fake))
    context = Mock()
    context.wrap_socket.side_effect = lambda sock, server_hostname: sock
    monkeypatch.setattr(web_tools.ssl, "create_default_context", Mock(return_value=context))
    web_tools._fetch_from_pinned_address("https://example.com/", "8.8.8.8")
    context.wrap_socket.assert_called_once_with(fake, server_hostname="example.com")


def test_peer_mismatch_is_rejected(monkeypatch):
    fake = FakeSocket("1.1.1.1")
    monkeypatch.setattr(web_tools.socket, "socket", Mock(return_value=fake))
    with pytest.raises(PermissionDenied, match="differs from validated address"):
        web_tools._fetch_from_pinned_address("http://example.com/", "8.8.8.8")


def test_pinned_fetch_never_resolves_hostname_again(monkeypatch):
    fake = FakeSocket("8.8.8.8")
    monkeypatch.setattr(web_tools.socket, "socket", Mock(return_value=fake))
    monkeypatch.setattr(web_tools.socket, "getaddrinfo", Mock(side_effect=AssertionError("unexpected DNS lookup")))
    status, headers, body, connected = web_tools.fetch_pinned(
        "http://example.com/", ["8.8.8.8"]
    )
    assert (status, connected) == (200, "8.8.8.8")
    assert body == b"hello"


def test_redirect_is_returned_not_followed(monkeypatch):
    fake = FakeSocket("8.8.8.8")
    fake.makefile = lambda mode: io.BytesIO(
        b"HTTP/1.1 302 Found\r\nLocation: https://127.0.0.1/\r\nContent-Length: 0\r\n\r\n"
    )
    monkeypatch.setattr(web_tools.socket, "socket", Mock(return_value=fake))
    status, headers, body, connected = web_tools.fetch_pinned("http://example.com/", ["8.8.8.8"])
    assert status == 302
    assert headers["location"] == "https://127.0.0.1/"
    assert body == b""
