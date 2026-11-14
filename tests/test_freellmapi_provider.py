"""Hermetic tests for the FreeLLMAPI OpenAI-compatible provider."""

from __future__ import annotations

from typing import Any

import pytest

from matrix_ai.config import ConfigError, MatrixConfig
from matrix_ai.errors import ModelUnavailable
from matrix_ai.models.base import (
    ChatMessage,
    FreeLLMAPIProvider,
    ModelResponse,
    build_provider,
)


class FakeResponse:
    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self.payload = payload

    def json(self) -> Any:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeAsyncClient:
    calls: list[dict[str, Any]] = []
    response: FakeResponse

    def __init__(self, *, timeout: float) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, "json": json, "headers": headers})
        return self.response

    async def get(self, url: str, *, headers: dict) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, "headers": headers})
        return self.response


def _configured(config: MatrixConfig) -> MatrixConfig:
    config.model_provider = "freellmapi"
    config.freellmapi_url = "http://127.0.0.1:3001/v1"
    config.freellmapi_api_key = "test-unified-key"
    config.allow_cloud_models = True
    return config


def test_default_freellmapi_endpoint_is_local_and_unconfigured(config: MatrixConfig) -> None:
    provider = FreeLLMAPIProvider(config)
    assert config.freellmapi_url == "http://127.0.0.1:3001/v1"
    assert config.freellmapi_api_key is None
    assert provider.configured is False


def test_freellmapi_requires_api_key_when_selected(tmp_path) -> None:
    with pytest.raises(ConfigError, match="MATRIX_FREELLMAPI_API_KEY"):
        MatrixConfig(home=tmp_path / "matrix", model_provider="freellmapi")


def test_freellmapi_rejects_remote_plain_http(tmp_path) -> None:
    with pytest.raises(ConfigError, match="must use HTTPS"):
        MatrixConfig(
            home=tmp_path / "matrix",
            model_provider="freellmapi",
            freellmapi_api_key="test-unified-key",
            freellmapi_url="http://api.example.com/v1",
        )


def test_provider_factory_selects_freellmapi(config: MatrixConfig) -> None:
    configured = _configured(config)
    assert isinstance(build_provider(configured), FreeLLMAPIProvider)


async def test_complete_requires_cloud_opt_in(config: MatrixConfig) -> None:
    config = _configured(config)
    config.allow_cloud_models = False
    provider = FreeLLMAPIProvider(config)
    with pytest.raises(ModelUnavailable, match="explicit cloud-model consent"):
        await provider.complete([ChatMessage(role="user", content="hello")])


async def test_complete_uses_documented_v1_route_and_bearer_key(config: MatrixConfig, monkeypatch) -> None:
    config = _configured(config)
    calls: list[dict[str, Any]] = []

    class Client(FakeAsyncClient):
        async def post(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
            calls.append({"url": url, "json": json, "headers": headers})
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": "hello from the local router"}}]},
            )

    monkeypatch.setattr("matrix_ai.models.base.httpx.AsyncClient", Client)
    provider = FreeLLMAPIProvider(config)
    result = await provider.complete(
        [ChatMessage(role="user", content="hello")],
        temperature=0.1,
        max_tokens=64,
        json_mode=True,
    )

    assert isinstance(result, ModelResponse)
    assert result.text == "hello from the local router"
    assert calls[0]["url"] == "http://127.0.0.1:3001/v1/chat/completions"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-unified-key"
    assert calls[0]["json"]["stream"] is False
    assert calls[0]["json"]["response_format"] == {"type": "json_object"}


async def test_probe_uses_bearer_key(config: MatrixConfig, monkeypatch) -> None:
    config = _configured(config)
    calls: list[dict[str, Any]] = []

    class Client(FakeAsyncClient):
        async def get(self, url: str, *, headers: dict) -> FakeResponse:
            calls.append({"url": url, "headers": headers})
            return FakeResponse(200, {"data": []})

    monkeypatch.setattr("matrix_ai.models.base.httpx.AsyncClient", Client)
    health = await FreeLLMAPIProvider(config)._probe()

    assert health.configured is True
    assert health.reachable is True
    assert health.status == "working"
    assert calls[0]["url"] == "http://127.0.0.1:3001/v1/models"
    assert calls[0]["headers"]["Authorization"] == "Bearer test-unified-key"


async def test_probe_reports_unconfigured_without_credentials(config: MatrixConfig) -> None:
    health = await FreeLLMAPIProvider(config)._probe()
    assert health.configured is False
    assert health.status == "unconfigured"


async def test_complete_rejects_non_json_and_empty_payloads(config: MatrixConfig, monkeypatch) -> None:
    config = _configured(config)

    class Client(FakeAsyncClient):
        async def post(self, url: str, *, json: dict, headers: dict) -> FakeResponse:
            return FakeResponse(200, [])

    monkeypatch.setattr("matrix_ai.models.base.httpx.AsyncClient", Client)
    with pytest.raises(ModelUnavailable, match="unexpected payload"):
        await FreeLLMAPIProvider(config).complete([ChatMessage(role="user", content="hello")])
