"""Tests for pinned external add-ons, MCP bridges and YouTube automation policy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from matrix_ai.core.models import Risk
from matrix_ai.errors import ConfigError, ToolUnavailable, ValidationFailure
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
