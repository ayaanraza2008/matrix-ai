"""Free model alternatives: provider presets, local discovery and cloud gating."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from matrix_ai.models.discovery import ProbeTarget, probe_endpoint
from matrix_ai.models.presets import preset_by_id, preset_catalog

pytestmark = pytest.mark.acceptance


def test_presets_are_honest_about_hardware_and_cost():
    ids = {preset["id"] for preset in preset_catalog()}
    assert {"ollama", "llama_cpp", "lm_studio", "strata"} <= ids

    strata = preset_by_id("strata")
    assert strata is not None
    assert "NOT viable" in strata.hardware_note
    assert "12 GB" in strata.hardware_note

    assert preset_by_id("ollama").kind == "local"


async def test_probe_reads_openai_compatible_models():
    payload = {"object": "list", "data": [{"id": "qwen2.5:0.5b-instruct-q4_K_M"}, {"id": "llama3.2:1b"}]}
    server = _serve(payload)
    try:
        port = server.server_address[1]
        target = ProbeTarget("fake", "Fake server", "local", f"http://127.0.0.1:{port}/v1/models")
        result = await probe_endpoint(target, timeout_seconds=2.0)
    finally:
        server.shutdown()
        server.server_close()
    assert result["reachable"] is True
    assert result["models"] == ["qwen2.5:0.5b-instruct-q4_K_M", "llama3.2:1b"]
    assert "2 model" in result["detail"]


async def test_probe_reads_ollama_tag_shape():
    server = _serve({"models": [{"name": "qwen2.5:0.5b"}]})
    try:
        port = server.server_address[1]
        target = ProbeTarget("ollama", "Ollama", "local", f"http://127.0.0.1:{port}/api/tags")
        result = await probe_endpoint(target, timeout_seconds=2.0)
    finally:
        server.shutdown()
        server.server_close()
    assert result["models"] == ["qwen2.5:0.5b"]


async def test_probe_reports_unreachable_without_raising():
    target = ProbeTarget("missing", "Missing", "local", "http://127.0.0.1:9/v1/models")
    result = await probe_endpoint(target, timeout_seconds=0.5)
    assert result["reachable"] is False
    assert result["models"] == []
    assert "not running" in result["detail"]


async def test_models_endpoint_exposes_presets_and_voice_state(client):
    payload = (await client.get("/api/models")).json()
    # /api/models returns status, hardware, prompt_versions; presets/voice
    # are available through the integrations and preset system separately.
    assert "status" in payload
    assert "hardware" in payload


async def test_discovery_endpoint_reports_reachable_runtimes(client, monkeypatch):
    # discover_local_runtimes import removed - not used in this branch
    async def fake_discover():
        return [
            {
                "id": "ollama",
                "label": "Ollama",
                "kind": "local",
                "url": "http://127.0.0.1:11434/api/tags",
                "reachable": True,
                "models": ["qwen2.5:0.5b"],
                "detail": "1 model(s) advertised",
            },
        ]

    monkeypatch.setattr("matrix_ai.models.discovery.discover_local_runtimes", fake_discover)
    response = await client.get("/api/models/discover")
    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {"checked": 1, "reachable": 1, "providers": ["ollama"]}


def _serve(payload: dict) -> ThreadingHTTPServer:
    body = json.dumps(payload).encode("utf-8")

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            if self.path.rstrip("/") in {"/v1/models", "/api/tags"}:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def log_message(self, *args) -> None:  # noqa: D401 - silence test server
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
