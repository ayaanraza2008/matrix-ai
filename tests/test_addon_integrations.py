"""Tests for pinned external add-ons, MCP bridges and YouTube automation policy."""

from __future__ import annotations

import sys
from dataclasses import replace

import pytest

from matrix_ai.core.models import Risk
from matrix_ai.errors import ConfigError, ToolExecutionError, ToolUnavailable, ValidationFailure
from matrix_ai.integrations.addon_catalog import ADDON_BY_ID, ADDONS
from matrix_ai.integrations.addons import AddonError
from matrix_ai.integrations.mcp_addons import MCPStdioClient
from matrix_ai.tools.base import ToolContext
from matrix_ai.tools.external_mcp import build_mcp_tools
from matrix_ai.integrations.youtube_automation import build_youtube_tools


class FakeMCPClient:
    async def call_tool(self, name, arguments):
        return {"called": name, "arguments": arguments}


def test_catalog_contains_all_eighteen_pinned_user_requested_repositories():
    assert len(ADDONS) == 18
    assert len(ADDON_BY_ID) == 18
    assert all(len(item["commit"]) == 40 for item in ADDONS)
    assert {item["id"] for item in ADDONS} >= {
        "openwhispr",
        "youtube-automation-agent",
        "craft-anything",
        "free-for-dev",
        "hexstrike-ai",
        "mobile-mcp",
        "awesome-free-llm-apis",
        "harness2",
        "g0dm0d3",
        "cal-diy",
        "rea",
        "ecc",
        "build-your-own-x",
        "openviking",
        "anthropic-cybersecurity-skills",
        "agentmemory",
        "scientific-agent-skills",
        "browser-use",
    }


def test_addon_manager_reports_lazy_sources_not_installed(runtime):
    status = runtime.addon_manager.status()
    assert status["count"] == 18
    assert status["installed_count"] == 0
    assert status["connected_count"] == 0
    assert all(item["installed"] is False for item in status["addons"])
    assert all("commit" in item for item in status["addons"])
    assert all("prompt" not in item for item in status["addons"])


def test_capability_router_finds_browser_youtube_voice_and_research(runtime):
    assert "browser-use" in {
        item["id"] for item in runtime.addon_manager.route(
            "Navigate the browser, inspect a web page, and fill out a form"
        )
    }
    assert "youtube-automation-agent" in {
        item["id"] for item in runtime.addon_manager.route(
            "Create YouTube videos, manage the channel, review analytics and schedule content"
        )
    }
    assert "openwhispr" in {
        item["id"] for item in runtime.addon_manager.route(
            "Listen to my voice, transcribe speech and understand spoken commands"
        )
    }
    assert "scientific-agent-skills" in {
        item["id"] for item in runtime.addon_manager.route(
            "Conduct scientific research in bioinformatics, chemistry and statistics"
        )
    }


def test_security_and_jailbreak_corpora_are_not_normal_prompt_context():
    assert ADDON_BY_ID["g0dm0d3"]["context_enabled"] is False
    assert ADDON_BY_ID["hexstrike-ai"]["context_enabled"] is False
    assert ADDON_BY_ID["harness2"]["context_enabled"] is False


def test_unknown_license_and_large_source_are_explicit():
    assert ADDON_BY_ID["free-for-dev"]["license"] == "UNKNOWN"
    assert ADDON_BY_ID["build-your-own-x"]["license"] == "UNKNOWN"
    assert ADDON_BY_ID["cal-diy"]["size_mb"] > 1000
    assert "non-production" in ADDON_BY_ID["cal-diy"]["install_note"]


def test_large_repositories_need_explicit_owner_opt_in(runtime):
    with pytest.raises(AddonError, match="allow_large=true"):
        runtime.addon_manager.install("cal-diy", allow_large=False)


def test_mcp_server_owner_configuration_keeps_credentials_out_of_public_settings(config):
    secret = "Bearer test-openwhispr-secret"
    configured = replace(
        config,
        addon_mcp_servers={
            "openwhispr": {
                "transport": "streamable_http",
                "url": "https://mcp.openwhispr.com/mcp",
                "headers": {"Authorization": secret},
                "read_only_tools": ["list_notes"],
            }
        },
    )
    public = str(configured.public_dict())
    assert "openwhispr" in public
    assert secret not in public
    assert configured.addon_mcp_servers["openwhispr"]["transport"] == "streamable_http"


def test_remote_mcp_transport_is_restricted_to_verified_openwhispr_host():
    with pytest.raises(ToolUnavailable, match="verified OpenWhispr"):
        MCPStdioClient(
            server_name="openwhispr",
            transport="streamable_http",
            url="https://evil.example/mcp",
            headers={"Authorization": "Bearer test"},
        )


def test_mcp_tool_annotations_cannot_self_grant_read_only_authority():
    definitions = [
        {
            "name": "browser_navigate",
            "description": "Navigate a browser page",
            "inputSchema": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
            "annotations": {"readOnlyHint": True},
        }
    ]
    client = FakeMCPClient()
    default_tools = build_mcp_tools("browser-use", definitions, client)
    assert default_tools[0].risk is Risk.HIGH
    assert default_tools[0].permission_scope == "addons.control"

    trusted_tools = build_mcp_tools(
        "browser-use", definitions, client, read_only_tools={"browser_navigate"}
    )
    assert trusted_tools[0].risk is Risk.LOW
    assert trusted_tools[0].permission_scope == "addons.read"


@pytest.mark.asyncio
async def test_external_mcp_tool_validates_arguments_and_marks_results_untrusted(config):
    definition = {
        "name": "read_page",
        "description": "Read the current page",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        "annotations": {"readOnlyHint": True},
    }
    client = FakeMCPClient()
    tool = build_mcp_tools(
        "browser-use", [definition], client, read_only_tools={"read_page"}
    )[0]
    validated = tool.validate({"url": "https://example.test"})
    context = ToolContext(task_id="test", config=config, scopes=["addons.read"])
    output = await tool.run(validated, context)
    assert output["called"] == "read_page"
    assert output["suspicious"] is True
    assert tool.verify(validated, output).ok
    with pytest.raises(ValidationFailure):
        tool.validate({"url": "https://example.test", "unexpected": True})


def test_youtube_service_tools_are_opt_in_and_split_by_risk(config):
    assert build_youtube_tools(config) == []
    enabled = replace(config, youtube_automation_enabled=True)
    tools = {tool.name: tool for tool in build_youtube_tools(enabled)}
    assert tools["youtube.dashboard"].risk is Risk.LOW
    assert tools["youtube.job_status"].risk is Risk.LOW
    assert tools["youtube.generate_video"].risk is Risk.HIGH
    assert tools["youtube.start_operator"].risk is Risk.CRITICAL
    assert tools["youtube.approve_content"].risk is Risk.CRITICAL
    assert tools["youtube.publish_now"].risk is Risk.CRITICAL


def test_youtube_tools_do_not_accept_public_untrusted_service_urls(config):
    with pytest.raises(ConfigError, match="loopback or private"):
        replace(
            config,
            youtube_automation_enabled=True,
            youtube_automation_url="https://public.example",
        )


def test_youtube_api_key_never_appears_in_settings(config):
    configured = replace(
        config,
        youtube_automation_enabled=True,
        youtube_automation_api_key="youtube-secret-key",
    )
    public = str(configured.public_dict())
    assert "youtube-secret-key" not in public
    assert configured.public_dict()["youtube_automation_api_key_present"] is True


@pytest.mark.asyncio
async def test_addon_api_routes_are_authenticated_and_report_sources(client, anon_client):
    anon = await anon_client.get("/api/addons")
    assert anon.status_code == 401

    response = await client.get("/api/addons")
    assert response.status_code == 200
    assert response.json()["count"] == 18

    routed = await client.get(
        "/api/addons/route",
        params={"q": "control a mobile Android device, tap, swipe, and test apps"},
    )
    assert routed.status_code == 200
    assert "mobile-mcp" in [item["id"] for item in routed.json()["addons"]]

    tools = await client.get("/api/addons/tools")
    assert tools.status_code == 200
    assert tools.json() == []


@pytest.mark.asyncio
async def test_addon_management_never_auto_installs_huge_repo_or_unknown_mcp(client):
    large = await client.post("/api/addons/cal-diy/install", json={"allow_large": False})
    assert large.status_code == 422
    assert "allow_large=true" in large.json()["message"]

    connect = await client.post("/api/addons/browser-use/connect")
    assert connect.status_code == 422
    assert "MATRIX_ADDON_MCP_SERVERS" in connect.json()["message"]


def test_hexstrike_requires_an_exact_owner_target_allowlist(config):
    tools = build_mcp_tools(
        "hexstrike-ai",
        [
            {
                "name": "nmap_scan",
                "description": "Scan target",
                "inputSchema": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "annotations": {"readOnlyHint": True},
            }
        ],
        FakeMCPClient(),
        read_only_tools={"nmap_scan"},
        target_allowlist=set(config.hexstrike_allowed_targets),
    )
    assert tools[0].risk is Risk.HIGH

    allowed_config = replace(config, hexstrike_allowed_targets=("127.0.0.1",))
    client = FakeMCPClient()
    allowed = build_mcp_tools(
        "hexstrike-ai",
        [
            {
                "name": "nmap_scan",
                "description": "Scan target",
                "inputSchema": {
                    "type": "object",
                    "properties": {"target": {"type": "string"}},
                    "required": ["target"],
                },
                "annotations": {"readOnlyHint": True},
            }
        ],
        client,
        read_only_tools={"nmap_scan"},
        target_allowlist=set(allowed_config.hexstrike_allowed_targets),
    )[0]
    args = allowed.validate({"target": "127.0.0.1"})
    ctx = ToolContext(task_id="test", config=allowed_config, scopes=["addons.control"])
    assert (await allowed.run(args, ctx))["called"] == "nmap_scan"

    with pytest.raises(ToolExecutionError, match="not in MATRIX_HEXSTRIKE_ALLOWED_TARGETS"):
        await allowed.run(
            allowed.validate({"target": "203.0.113.99"}),
            ctx,
        )


@pytest.mark.asyncio
async def test_stdio_mcp_server_initializes_lists_and_calls_tools(tmp_path):
    server_file = tmp_path / "fake_mcp_server.py"
    server_file.write_text(
        "import json, sys\n"
        "for line in sys.stdin:\n"
        "    message = json.loads(line)\n"
        "    method = message.get('method')\n"
        "    if 'id' not in message:\n"
        "        continue\n"
        "    if method == 'initialize':\n"
        "        result = {'protocolVersion': '2025-06-18', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fake', 'version': '1'}}\n"
        "    elif method == 'tools/list':\n"
        "        result = {'tools': [{'name': 'ping', 'description': 'Ping', 'inputSchema': {'type': 'object', 'properties': {}}}]}\n"
        "    elif method == 'tools/call':\n"
        "        result = {'content': [{'type': 'text', 'text': 'pong'}]}\n"
        "    else:\n"
        "        result = {}\n"
        "    print(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': result}), flush=True)\n",
        encoding="utf-8",
    )
    client = MCPStdioClient(
        command=sys.executable,
        args=[str(server_file)],
        env={},
        server_name="test",
        request_timeout=5,
    )
    tools = await client.connect()
    try:
        assert [tool["name"] for tool in tools] == ["ping"]
        output = await client.call_tool("ping", {})
        assert output["content"] == ["pong"]
        assert output["_untrusted_mcp_result"] is True
    finally:
        await client.close()


def test_hexstrike_target_configuration_rejects_urls_and_wildcards(config):
    assert replace(config, hexstrike_allowed_targets=("localhost", "127.0.0.1", "::1")).hexstrike_allowed_targets == (
        "::1", "127.0.0.1", "localhost"
    )
    with pytest.raises(ConfigError):
        replace(config, hexstrike_allowed_targets=("https://example.test",))
    with pytest.raises(ConfigError):
        replace(config, hexstrike_allowed_targets=("*.example.test",))


@pytest.mark.asyncio
async def test_selected_safe_sources_are_lazily_fetched_and_security_sources_skipped(runtime, monkeypatch):
    from matrix_ai.integrations.addon_catalog import ADDON_BY_ID

    runtime.config.addon_auto_install_sources = True
    installed = []
    connected = []

    def fake_install(addon_id, *, allow_large=False):
        installed.append((addon_id, allow_large))
        return {"commit": ADDON_BY_ID[addon_id]["commit"]}

    async def fake_connect(addon_id, _registry):
        connected.append(addon_id)
        return {"status": "connected"}

    monkeypatch.setattr(runtime.addon_manager, "_installed_info", lambda _addon_id: None)
    monkeypatch.setattr(runtime.addon_manager, "install", fake_install)
    monkeypatch.setattr(runtime.addon_manager, "connect", fake_connect)
    runtime.config.addon_mcp_servers["hexstrike-ai"] = {
        "command": "python",
        "args": ["hexstrike_mcp.py"],
        "env": {},
        "read_only_tools": [],
    }
    selected = [
        {"id": "ecc"},
        {"id": "scientific-agent-skills"},
        {"id": "cal-diy"},
        {"id": "g0dm0d3"},
        {"id": "hexstrike-ai"},
    ]
    await runtime.addon_manager.prepare_selected(selected, runtime.registry)

    assert installed == [("ecc", False)]
    assert connected == []
    assert all(addon_id not in {"g0dm0d3", "hexstrike-ai", "harness2"} for addon_id, _ in installed)


@pytest.mark.asyncio
async def test_selected_owner_configured_browser_mcp_auto_connects(runtime, monkeypatch):
    connected = []
    runtime.config.addon_mcp_servers["browser-use"] = {
        "transport": "stdio",
        "command": "uvx",
        "args": ["browser-use"],
        "env": {},
        "read_only_tools": [],
    }

    async def fake_connect(addon_id, _registry):
        connected.append(addon_id)
        return {"status": "connected"}

    monkeypatch.setattr(runtime.addon_manager, "connect", fake_connect)
    await runtime.addon_manager.prepare_selected(
        [{"id": "browser-use"}],
        runtime.registry,
    )
    assert connected == ["browser-use"]


@pytest.mark.asyncio
async def test_hexstrike_without_target_is_blocked_even_when_allowlist_has_entries(config):
    definition = {
        "name": "run_security_action",
        "description": "Run a scan",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": {"readOnlyHint": True},
    }
    tool = build_mcp_tools(
        "hexstrike-ai",
        [definition],
        FakeMCPClient(),
        read_only_tools={"run_security_action"},
        target_allowlist={"127.0.0.1"},
    )[0]
    args = tool.validate({})
    ctx = ToolContext(task_id="test", config=replace(config, hexstrike_allowed_targets=("127.0.0.1",)), scopes=["addons.control"])
    with pytest.raises(ToolExecutionError, match="requires an explicit target"):
        await tool.run(args, ctx)


def test_unknown_license_catalogues_cannot_be_cloned(runtime):
    with pytest.raises(AddonError, match="link-only catalogue"):
        runtime.addon_manager.install("free-for-dev")
    with pytest.raises(AddonError, match="link-only catalogue"):
        runtime.addon_manager.install("build-your-own-x")
