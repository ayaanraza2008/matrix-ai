"""Model provider abstraction.

MATRIX never talks to a vendor SDK directly: the orchestrator depends on this
interface, so swapping llama.cpp / Ollama / an optional cloud endpoint does not
touch agent code. Cloud endpoints are refused unless explicitly enabled.
"""

from __future__ import annotations

import ipaddress
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal
from urllib.parse import urlparse

import httpx

from ..config import MatrixConfig
from ..core.models import iso
from ..errors import ModelUnavailable

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderHealth:
    configured: bool
    reachable: bool
    status: str
    detail: str | None = None
    checked_at: str = field(default_factory=lambda: iso())


def is_local_endpoint(url: str) -> bool:
    """True when a URL points at loopback/private infrastructure."""
    try:
        host = urlparse(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    if host in {"localhost", "localhost.localdomain"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith(".local") or host.endswith(".internal")
    return address.is_loopback or address.is_private or address.is_link_local


class ModelProvider(ABC):
    provider_name: ClassVar[str] = "base"

    def __init__(self, config: MatrixConfig) -> None:
        self.config = config
        self.model = config.model_name
        self.last_latency_ms: float | None = None
        self._last_health: ProviderHealth | None = None
        self._health_checked_at: float = 0.0

    @property
    def configured(self) -> bool:
        return False

    @abstractmethod
    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        json_mode: bool = False,
    ) -> ModelResponse: ...

    @abstractmethod
    async def _probe(self) -> ProviderHealth: ...

    async def health(self, *, max_age_seconds: float = 30.0) -> ProviderHealth:
        now = time.monotonic()
        if self._last_health is not None and (now - self._health_checked_at) < max_age_seconds:
            return self._last_health
        try:
            health = await self._probe()
        except Exception as exc:  # pragma: no cover - defensive
            health = ProviderHealth(
                configured=self.configured,
                reachable=False,
                status="unavailable",
                detail=f"{type(exc).__name__}: {exc}",
            )
        self._last_health = health
        self._health_checked_at = now
        return health


# ``provider_name`` is a ClassVar on the ABC above; each provider overrides it.


class NoModelProvider(ModelProvider):
    provider_name = "none"
    model = "none"

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        raise ModelUnavailable(
            "No local language model is configured. Set MATRIX_MODEL_PROVIDER=ollama and pull a model, "
            "or point MATRIX_OPENAI_BASE_URL at a local llama.cpp server."
        )

    async def _probe(self) -> ProviderHealth:
        return ProviderHealth(
            configured=False,
            reachable=False,
            status="unconfigured",
            detail=(
                "Deterministic tools and memory work without a model. To enable chat and free-form "
                "planning, configure Ollama or a local OpenAI-compatible server."
            ),
        )


class OllamaProvider(ModelProvider):
    provider_name = "ollama"

    @property
    def configured(self) -> bool:
        return bool(self.config.ollama_url)

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.model_request_timeout_s) as client:
                response = await client.post(f"{self.config.ollama_url.rstrip('/')}/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"Ollama is unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ModelUnavailable(f"Ollama returned HTTP {response.status_code}")
        data = response.json()
        text = (data.get("message") or {}).get("content", "")
        latency = (time.perf_counter() - started) * 1000
        self.last_latency_ms = latency
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailable("Ollama returned an empty completion")
        return ModelResponse(text=text, provider="ollama", model=self.model, latency_ms=latency)

    async def _probe(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.config.ollama_url.rstrip('/')}/api/tags")
        except httpx.HTTPError as exc:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="unavailable",
                detail=f"Ollama not reachable at {self.config.ollama_url} ({type(exc).__name__})",
            )
        if response.status_code != 200:
            return ProviderHealth(
                configured=True, reachable=False, status="degraded", detail=f"HTTP {response.status_code}"
            )
        models = [entry.get("name", "") for entry in response.json().get("models", [])]
        present = any(self.model.split(":")[0] in name for name in models)
        return ProviderHealth(
            configured=True,
            reachable=True,
            status="working" if present else "degraded",
            detail=None if present else f"model '{self.model}' is not pulled yet (ollama pull {self.model})",
        )


class OpenAICompatibleProvider(ModelProvider):
    provider_name = "openai"

    @property
    def configured(self) -> bool:
        return bool(self.config.openai_base_url)

    def _guard_cloud(self) -> None:
        if not is_local_endpoint(self.config.openai_base_url) and not self.config.allow_cloud_models:
            raise ModelUnavailable(
                "This endpoint is not local. Cloud models are opt-in: set MATRIX_ALLOW_CLOUD_MODELS=true "
                "to acknowledge that prompts leave this device."
            )

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        self._guard_cloud()
        headers = {"Content-Type": "application/json"}
        if self.config.openai_api_key:
            headers["Authorization"] = f"Bearer {self.config.openai_api_key}"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.model_request_timeout_s) as client:
                response = await client.post(
                    f"{self.config.openai_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"local model server unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ModelUnavailable(f"model server returned HTTP {response.status_code}")
        data = response.json()
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable("model server returned an unexpected payload") from exc
        latency = (time.perf_counter() - started) * 1000
        self.last_latency_ms = latency
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailable("model server returned an empty completion")
        return ModelResponse(text=text, provider="openai", model=self.model, latency_ms=latency)

    async def _probe(self) -> ProviderHealth:
        if not is_local_endpoint(self.config.openai_base_url) and not self.config.allow_cloud_models:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="blocked",
                detail="Non-local endpoint is blocked until MATRIX_ALLOW_CLOUD_MODELS=true.",
            )
        headers = {}
        if self.config.openai_api_key:
            headers["Authorization"] = f"Bearer {self.config.openai_api_key}"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{self.config.openai_base_url.rstrip('/')}/models", headers=headers)
        except httpx.HTTPError as exc:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="unavailable",
                detail=f"model server not reachable ({type(exc).__name__})",
            )
        if response.status_code != 200:
            return ProviderHealth(
                configured=True, reachable=False, status="degraded", detail=f"HTTP {response.status_code}"
            )
        return ProviderHealth(configured=True, reachable=True, status="working")



class FreeLLMAPIProvider(ModelProvider):
    """OpenAI-compatible client for the local FreeLLMAPI router."""

    provider_name = "freellmapi"

    @property
    def configured(self) -> bool:
        return bool(self.config.freellmapi_url and self.config.freellmapi_api_key)

    def _guard_cloud(self) -> None:
        # The router is local, but its upstream model requests can leave the device.
        if not self.config.allow_cloud_models:
            raise ModelUnavailable(
                "FreeLLMAPI requires explicit cloud-model consent. "
                "Set MATRIX_ALLOW_CLOUD_MODELS=true only after reviewing the data-sharing implications."
            )

    async def complete(self, messages, *, temperature=0.2, max_tokens=512, json_mode=False):
        if not self.configured:
            raise ModelUnavailable(
                "FreeLLMAPI is not configured. Set MATRIX_FREELLMAPI_API_KEY and MATRIX_FREELLMAPI_URL."
            )
        self._guard_cloud()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config.freellmapi_api_key}",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.to_dict() for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        started = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self.config.model_request_timeout_s) as client:
                response = await client.post(
                    f"{self.config.freellmapi_url.rstrip('/')}/chat/completions",
                    json=payload,
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise ModelUnavailable(f"FreeLLMAPI is unreachable: {type(exc).__name__}") from exc
        if response.status_code != 200:
            raise ModelUnavailable(f"FreeLLMAPI returned HTTP {response.status_code}")
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelUnavailable("FreeLLMAPI returned an unexpected payload") from exc
        latency = (time.perf_counter() - started) * 1000
        self.last_latency_ms = latency
        if not isinstance(text, str) or not text.strip():
            raise ModelUnavailable("FreeLLMAPI returned an empty completion")
        return ModelResponse(text=text, provider="freellmapi", model=self.model, latency_ms=latency)

    async def _probe(self) -> ProviderHealth:
        if not self.configured:
            return ProviderHealth(
                configured=False,
                reachable=False,
                status="unconfigured",
                detail="Set MATRIX_FREELLMAPI_API_KEY and MATRIX_FREELLMAPI_URL to configure FreeLLMAPI.",
            )
        if not self.config.allow_cloud_models:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="blocked",
                detail="FreeLLMAPI is configured but blocked until MATRIX_ALLOW_CLOUD_MODELS=true.",
            )
        headers = {"Authorization": f"Bearer {self.config.freellmapi_api_key}"}
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(
                    f"{self.config.freellmapi_url.rstrip('/')}/models",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="unavailable",
                detail=f"FreeLLMAPI not reachable ({type(exc).__name__})",
            )
        if response.status_code != 200:
            return ProviderHealth(
                configured=True,
                reachable=False,
                status="degraded",
                detail=f"HTTP {response.status_code}",
            )
        return ProviderHealth(configured=True, reachable=True, status="working")


def build_provider(config: MatrixConfig, provider_name: str | None = None) -> ModelProvider:
    name = (provider_name or config.model_provider).lower()
    if name == "ollama":
        return OllamaProvider(config)
    if name == "openai":
        return OpenAICompatibleProvider(config)
    if name == "freellmapi":
        return FreeLLMAPIProvider(config)
    return NoModelProvider(config)


__all__ = [
    "ChatMessage",
    "ModelProvider",
    "ModelResponse",
    "NoModelProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "FreeLLMAPIProvider",
    "ProviderHealth",
    "build_provider",
    "is_local_endpoint",
]
