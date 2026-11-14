"""Unit tests for AI provider fallback routing, health reporting, and config validation."""

from unittest.mock import AsyncMock

import pytest

from matrix_ai.config import ConfigError, MatrixConfig
from matrix_ai.errors import ModelUnavailable
from matrix_ai.models.base import ChatMessage, ModelResponse, ProviderHealth
from matrix_ai.models.manager import ModelManager


class FakeStore:
    def __init__(self):
        self.audits = []

    def audit(self, event_type, **kwargs):
        self.audits.append((event_type, kwargs))


def test_fallback_config_validation():
    config = MatrixConfig(model_provider="ollama", model_fallback_provider="openai")
    assert config.model_fallback_provider == "openai"

    with pytest.raises(ConfigError, match="MATRIX_MODEL_FALLBACK_PROVIDER"):
        MatrixConfig(model_fallback_provider="invalid_provider")


@pytest.mark.asyncio
async def test_fallback_not_called_when_primary_succeeds():
    config = MatrixConfig(model_provider="ollama", model_fallback_provider="openai")
    store = FakeStore()
    manager = ModelManager(config, store=store)

    manager.provider.complete = AsyncMock(
        return_value=ModelResponse(text="primary response", provider="ollama", model="test-m", latency_ms=12.0)
    )
    manager.fallback_provider.complete = AsyncMock()

    res = await manager.complete([ChatMessage(role="user", content="hello")])

    assert res.text == "primary response"
    assert res.provider == "ollama"
    manager.fallback_provider.complete.assert_not_called()
    assert any(a[0] == "model.completed" for a in store.audits)
    assert not any(a[0] == "model.fallback_used" for a in store.audits)


@pytest.mark.asyncio
async def test_fallback_called_when_primary_fails():
    config = MatrixConfig(
        model_provider="ollama",
        model_fallback_provider="openai",
        openai_base_url="http://127.0.0.1:8080/v1",
    )
    store = FakeStore()
    manager = ModelManager(config, store=store)

    manager.provider.complete = AsyncMock(side_effect=ModelUnavailable("Ollama is down"))
    manager.fallback_provider.complete = AsyncMock(
        return_value=ModelResponse(text="fallback response", provider="openai", model="test-m", latency_ms=45.0)
    )

    res = await manager.complete([ChatMessage(role="user", content="hello")])

    assert res.text == "fallback response"
    assert res.provider == "openai"
    assert any(a[0] == "model.fallback_used" for a in store.audits)
    fallback_event = next(a for a in store.audits if a[0] == "model.fallback_used")
    assert fallback_event[1]["detail"]["primary_provider"] == "ollama"
    assert fallback_event[1]["detail"]["fallback_provider"] == "openai"


@pytest.mark.asyncio
async def test_fallback_both_fail_raises():
    config = MatrixConfig(
        model_provider="ollama",
        model_fallback_provider="openai",
        openai_base_url="http://127.0.0.1:8080/v1",
    )
    store = FakeStore()
    manager = ModelManager(config, store=store)

    manager.provider.complete = AsyncMock(side_effect=ModelUnavailable("Ollama is down"))
    manager.fallback_provider.complete = AsyncMock(side_effect=ModelUnavailable("OpenAI is also down"))

    with pytest.raises(ModelUnavailable, match="Ollama is down"):
        await manager.complete([ChatMessage(role="user", content="hello")])

    assert "Primary failed" in (manager.last_error or "")
    assert "Fallback failed" in (manager.last_error or "")


@pytest.mark.asyncio
async def test_status_reports_fallback_provider():
    config = MatrixConfig(
        model_provider="ollama",
        model_fallback_provider="openai",
        openai_base_url="http://127.0.0.1:8080/v1",
    )
    manager = ModelManager(config)

    manager.provider.health = AsyncMock(
        return_value=ProviderHealth(configured=True, reachable=True, status="working")
    )
    manager.fallback_provider.health = AsyncMock(
        return_value=ProviderHealth(configured=True, reachable=False, status="degraded")
    )

    st = await manager.status()
    assert st.provider == "ollama"
    assert st.status == "working"
    assert st.fallback_provider == "openai"
    assert st.fallback_status == "degraded"
