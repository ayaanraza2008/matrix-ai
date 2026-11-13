"""SSRF guards and provider boundaries."""

from __future__ import annotations

import socket

import pytest

from matrix_ai.errors import ModelUnavailable, PermissionDenied, ToolExecutionError
from matrix_ai.config import MatrixConfig
from matrix_ai.models.base import OpenAICompatibleProvider, is_local_endpoint
from matrix_ai.tools.web_tools import html_to_text, resolve_and_validate


def _resolver(ip: str):
    def resolve(host, port, **kwargs):
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port))]

    return resolve


def test_rejects_non_public_addresses():
    for ip in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254", "::1"):
        with pytest.raises(PermissionDenied):
            resolve_and_validate("http://internal.example.com", _resolver(ip))


def test_rejects_literal_loopback():
    with pytest.raises(PermissionDenied):
        resolve_and_validate("http://127.0.0.1/admin", _resolver("127.0.0.1"))


def test_rejects_non_http_schemes():
    for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://example.com"):
        with pytest.raises(PermissionDenied):
            resolve_and_validate(url)


def test_rejects_unusual_ports():
    with pytest.raises(PermissionDenied):
        resolve_and_validate("http://example.com:22/", _resolver("93.184.216.34"))


def test_rejects_embedded_credentials():
    with pytest.raises(PermissionDenied):
        resolve_and_validate("http://user:pass@example.com/", _resolver("93.184.216.34"))


def test_accepts_public_address():
    addresses = resolve_and_validate("https://example.com/page", _resolver("93.184.216.34"))
    assert addresses == ["93.184.216.34"]


def test_unresolvable_host_is_a_tool_error():
    def failing(host, port, **kwargs):
        raise socket.gaierror("no such host")

    with pytest.raises(ToolExecutionError):
        resolve_and_validate("https://nope.invalid/", failing)


def test_html_to_text_strips_markup_and_scripts():
    raw = "<html><head><title>T</title><script>alert(1)</script></head><body><p>Hello</p><p>World</p></body></html>"
    text = html_to_text(raw)
    assert "Hello" in text
    assert "World" in text
    assert "<p>" not in text
    assert "alert(1)" not in text


def test_local_endpoint_detection():
    assert is_local_endpoint("http://127.0.0.1:8080/v1")
    assert is_local_endpoint("http://192.168.1.50:11434")
    assert is_local_endpoint("http://localhost:8080/v1")
    assert not is_local_endpoint("https://api.openai.com/v1")


async def test_cloud_endpoint_requires_explicit_opt_in(config):
    config.model_provider = "openai"
    config.openai_base_url = "https://api.example.com/v1"
    config.allow_cloud_models = False
    provider = OpenAICompatibleProvider(config)
    with pytest.raises(ModelUnavailable):
        await provider.complete([])


def test_local_provider_is_not_blocked_by_cloud_guard(config):
    config.model_provider = "openai"
    config.openai_base_url = "http://127.0.0.1:8080/v1"
    provider = OpenAICompatibleProvider(config)
    provider._guard_cloud()  # must not raise


def test_public_web_fetch_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MATRIX_ALLOW_WEB_FETCH", raising=False)
    config = MatrixConfig.from_env()
    assert config.allow_web_fetch is False


def test_public_web_fetch_requires_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("MATRIX_ALLOW_WEB_FETCH", "true")
    config = MatrixConfig.from_env()
    assert config.allow_web_fetch is True
