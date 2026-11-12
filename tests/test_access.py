from __future__ import annotations

import json
import subprocess

import pytest

from matrix_ai.access import REMOTE_TARGET, RemoteAccessManager, StartupTaskManager


def completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def test_remote_access_enable_uses_private_serve_target_only(monkeypatch):
    calls = []
    monkeypatch.setattr("matrix_ai.access.shutil.which", lambda _name: "tailscale")

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1:] == ["status", "--json"]:
            return completed(json.dumps({"BackendState": "Running", "Self": {"HostName": "matrix-pc"}}))
        if argv[1:] == ["serve", "status"]:
            return completed("")
        if argv[1:] == ["serve", "--bg", REMOTE_TARGET]:
            return completed("Available within your tailnet: https://matrix-pc.example.ts.net")
        raise AssertionError(f"unexpected command: {argv}")

    monkeypatch.setattr("matrix_ai.access._run", fake_run)
    result = RemoteAccessManager().enable()
    assert result["scope"] == "tailnet_only"
    assert result["public_funnel_enabled_by_matrix"] is False
    assert calls[-1] == ["tailscale", "serve", "--bg", REMOTE_TARGET]
    assert all("funnel" not in part.lower() for call in calls for part in call)


def test_remote_access_refuses_to_replace_unrelated_serve_route(monkeypatch):
    monkeypatch.setattr("matrix_ai.access.shutil.which", lambda _name: "tailscale")

    def fake_run(argv, **kwargs):
        if argv[1:] == ["status", "--json"]:
            return completed(json.dumps({"BackendState": "Running", "Self": {"HostName": "matrix-pc"}}))
        if argv[1:] == ["serve", "status"]:
            return completed("https://other-app.example.ts.net -> http://127.0.0.1:3000")
        raise AssertionError(argv)

    monkeypatch.setattr("matrix_ai.access._run", fake_run)
    with pytest.raises(RuntimeError, match="Another Tailscale Serve route"):
        RemoteAccessManager().enable()


def test_remote_access_missing_cli_is_reported_without_network(monkeypatch):
    monkeypatch.setattr("matrix_ai.access.shutil.which", lambda _name: None)
    result = RemoteAccessManager().status()
    assert result["installed"] is False
    assert result["serve_enabled"] is False
    assert result["scope"] == "tailnet_only"


def test_startup_task_installs_only_for_interactive_user(monkeypatch):
    monkeypatch.setattr("matrix_ai.access.sys.platform", "win32")
    monkeypatch.setattr("matrix_ai.access.shutil.which", lambda _name: "schtasks.exe")
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return completed("SUCCESS")

    monkeypatch.setattr("matrix_ai.access._run", fake_run)
    result = StartupTaskManager().install()
    argv = captured["argv"]
    assert argv[1:3] == ["/Create", "/TN"]
    assert "ONLOGON" in argv
    assert "/IT" in argv
    assert "/RL" in argv and "LIMITED" in argv
    assert result["installed"] is True


def test_startup_task_is_platform_gated(monkeypatch):
    monkeypatch.setattr("matrix_ai.access.sys.platform", "linux")
    with pytest.raises(RuntimeError, match="Windows only"):
        StartupTaskManager().install()


@pytest.mark.asyncio
async def test_api_exposes_authenticated_autonomy_and_remote_access_status(client, monkeypatch):
    from matrix_ai.api.routes import RemoteAccessManager

    monkeypatch.setattr(
        RemoteAccessManager,
        "status",
        lambda self: {
            "installed": True,
            "connected": True,
            "serve_enabled": False,
            "endpoint": None,
            "scope": "tailnet_only",
            "public_funnel_enabled_by_matrix": False,
            "detail": "Tailscale is connected but MATRIX Serve is not enabled.",
        },
    )
    automation = await client.get("/api/automation/status")
    remote = await client.get("/api/access/remote")

    assert automation.status_code == 200
    assert automation.json()["maximum_scheduled_job_autonomy"] == 2
    assert automation.json()["policy"]["high_risk_requires_exact_approval"] is True
    assert remote.status_code == 200
    assert remote.json()["scope"] == "tailnet_only"
    assert remote.json()["public_funnel_enabled_by_matrix"] is False
