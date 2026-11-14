"""Unit and integration tests for VPS-free autonomous cloud architecture.

Tests GitStateSync, single-shot Telegram sync_once, Orchestrator drain,
GeminiProvider, and run_cloud_step.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from matrix_ai.cloud_native import (
    DEFAULT_STATE_DB_RELPATH,
    CloudStepResult,
    GitStateSync,
    run_cloud_step,
)
from matrix_ai.config import MatrixConfig
from matrix_ai.core.models import TaskState
from matrix_ai.models.base import ChatMessage, GeminiProvider
from matrix_ai.telegram import TelegramChatBridge


@pytest.fixture
def temp_git_repo(tmp_path: Path):
    """Fixture providing an initialized local Git repository."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    (repo / "README.md").write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=repo, check=True)
    return repo


def test_git_state_sync_integrity(tmp_path: Path):
    db_file = tmp_path / "test.db"
    # 1. Non-existent / empty
    assert GitStateSync.verify_integrity(db_file) == "empty"

    # 2. Valid SQLite database
    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE foo (id INT)")
    con.execute("INSERT INTO foo VALUES (1)")
    con.commit()
    con.close()
    assert GitStateSync.verify_integrity(db_file) == "ok"

    # 3. Corrupt file
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(b"corrupt junk data that is not a sqlite database header")
    assert GitStateSync.verify_integrity(corrupt) != "ok"


def test_git_state_sync_push_and_pull_roundtrip(temp_git_repo: Path):
    sync = GitStateSync(repo_dir=temp_git_repo, branch="matrix-data")
    assert sync.branch == "matrix-data"
    db_file = temp_git_repo / "data" / "matrix.db"
    db_file.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_file))
    con.execute("CREATE TABLE items (id TEXT PRIMARY KEY, val TEXT)")
    con.execute("INSERT INTO items VALUES ('k1', 'state_data_1')")
    con.commit()
    con.close()

    # In local repo without remote, test plumbing commit locally
    # 1. Blob hash
    p = subprocess.run(["git", "hash-object", "-w", str(db_file)], cwd=temp_git_repo, check=True, capture_output=True, text=True)
    blob_sha = p.stdout.strip()
    assert blob_sha

    # 2. Write tree and commit into matrix-data branch
    env = os.environ.copy()
    idx_file = temp_git_repo / ".git" / "temp_idx"
    env["GIT_INDEX_FILE"] = str(idx_file)
    subprocess.run(["git", "update-index", "--add", "--cacheinfo", "100644", blob_sha, DEFAULT_STATE_DB_RELPATH], cwd=temp_git_repo, check=True, env=env)
    p = subprocess.run(["git", "write-tree"], cwd=temp_git_repo, check=True, capture_output=True, text=True, env=env)
    tree_sha = p.stdout.strip()
    p = subprocess.run(["git", "commit-tree", tree_sha, "-m", "chore: persist state"], cwd=temp_git_repo, check=True, capture_output=True, text=True)
    commit_sha = p.stdout.strip()
    subprocess.run(["git", "update-ref", "refs/heads/matrix-data", commit_sha], cwd=temp_git_repo, check=True)

    # 3. Read back with git show
    p = subprocess.run(["git", "show", f"matrix-data:{DEFAULT_STATE_DB_RELPATH}"], cwd=temp_git_repo, check=True, capture_output=True)
    restored_db = temp_git_repo / "restored.db"
    restored_db.write_bytes(p.stdout)
    assert GitStateSync.verify_integrity(restored_db) == "ok"

    con2 = sqlite3.connect(str(restored_db))
    row = con2.execute("SELECT val FROM items WHERE id='k1'").fetchone()
    con2.close()
    assert row[0] == "state_data_1"


@pytest.mark.asyncio
async def test_telegram_sync_once_processes_run_command(tmp_path: Path):
    config = MatrixConfig(
        home=tmp_path,
        telegram_bot_token="test-token",
        telegram_chat_id="123456789",
    )

    class FakeStore:
        def __init__(self):
            self.audits = []
            self.flags = {}
            self.tasks = {}

        def audit(self, evt, **kw):
            self.audits.append((evt, kw))

        def get_system_flag(self, k):
            return self.flags.get(k)

        def set_system_flag(self, k, v):
            self.flags[k] = v

        def get_idempotent_task_id(self, scope, key):
            return None

        def task_view(self, tid):
            return SimpleNamespace(state=TaskState.RECEIVED, summary=None, error=None)

    class FakeOrchestrator:
        def __init__(self):
            self.created = []
            self.enqueued = []

        def create_task(self, **kwargs):
            self.created.append(kwargs)
            return "task-run-456"

        async def enqueue(self, tid):
            self.enqueued.append(tid)

    store = FakeStore()
    orchestrator = FakeOrchestrator()
    bridge = TelegramChatBridge(config=config, orchestrator=orchestrator, store=store)

    sent = []

    async def fake_send(text):
        sent.append(text)

    bridge._send = fake_send

    mock_updates = {
        "ok": True,
        "result": [
            {
                "update_id": 101,
                "message": {
                    "chat": {"id": 123456789, "type": "private"},
                    "from": {"id": 123456789},
                    "text": "/run Check disk usage and verify memory",
                },
            }
        ],
    }

    async def fake_api(method, payload):
        if method == "getUpdates":
            return mock_updates
        if method == "setMyCommands":
            return {"ok": True}
        return {"ok": True}

    bridge._api = fake_api
    bridge._client = AsyncMock()

    processed = await bridge.sync_once(max_updates=10)

    assert processed == 1
    assert orchestrator.created[0]["request"] == "Check disk usage and verify memory"
    assert orchestrator.enqueued == ["task-run-456"]
    assert store.flags.get("telegram_update_offset") == "101"
    # Verify staged output
    assert any("1. Command received: /run" in s for s in sent)
    assert any("2. Task queued" in s for s in sent)


@pytest.mark.asyncio
async def test_telegram_flush_reports(tmp_path: Path):
    config = MatrixConfig(home=tmp_path, telegram_bot_token="tok", telegram_chat_id="123")
    bridge = TelegramChatBridge(config=config, orchestrator=None, store=None)

    async def dummy_report():
        await asyncio.sleep(0.01)

    t1 = asyncio.create_task(dummy_report())
    bridge._report_tasks.add(t1)

    flushed = await bridge.flush_reports(timeout=1.0)
    assert flushed == 1
    assert t1.done()


@pytest.mark.asyncio
async def test_orchestrator_drain():
    from matrix_ai.core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch._runners = {}

    # Empty drain
    assert await orch.drain(timeout=0.1) == 0

    # Drain with completed task
    async def dummy_job():
        await asyncio.sleep(0.02)

    t = asyncio.create_task(dummy_job())
    orch._runners["t1"] = t

    completed = await orch.drain(timeout=1.0)
    assert completed == 1
    assert t.done()


@pytest.mark.asyncio
async def test_gemini_provider_guard_and_complete():
    config = MatrixConfig(
        gemini_api_key="test-gemini-key",
        gemini_model="gemini-1.5-flash",
        allow_cloud_models=False,
    )
    provider = GeminiProvider(config)
    assert provider.configured is True

    # 1. Cloud guard without consent raises ModelUnavailable
    from matrix_ai.errors import ModelUnavailable

    with pytest.raises(ModelUnavailable, match="explicit consent"):
        await provider.complete([ChatMessage(role="user", content="hello")])

    # 2. Probe when blocked
    health = await provider.health(max_age_seconds=0.0)
    assert health.status == "blocked"

    # 3. Successful completion when cloud consent given
    config.allow_cloud_models = True

    mock_resp = SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "Gemini response text"}}]},
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
        res = await provider.complete([ChatMessage(role="user", content="hello")])
        assert res.text == "Gemini response text"
        assert res.provider == "gemini"
        assert res.model == "gemini-1.5-flash"


@pytest.mark.asyncio
async def test_run_cloud_step_isolated(tmp_path: Path):
    db_path = tmp_path / "matrix.db"
    config = MatrixConfig(
        home=tmp_path,
        db_path=db_path,
        model_provider="none",
        telegram_bot_token=None,  # disabled
    )

    result: CloudStepResult = await run_cloud_step(
        config=config,
        repo_dir=tmp_path,
        pull_state_enabled=False,
        push_state_enabled=False,
        sync_telegram_enabled=False,
        drain_timeout=1.0,
    )

    assert result.duration_seconds >= 0.0
    assert result.db_integrity == "ok"
    assert result.primary_provider == "none"
    assert result.fallback_provider == "none"
    assert result.tasks_completed == 0
    assert "MATRIX AI Cloud Execution Step" in result.summary_markdown()


@pytest.mark.asyncio
async def test_run_cloud_step_with_webhook_incoming_update(tmp_path: Path):
    db_path = tmp_path / "matrix.db"
    config = MatrixConfig(
        home=tmp_path,
        db_path=db_path,
        model_provider="none",
        telegram_bot_token="test-token",
        telegram_chat_id="123456789",
        telegram_mode="webhook",
        telegram_webhook_secret="sec-123",
    )

    update_payload = {
        "update_id": 999,
        "message": {
            "chat": {"id": 123456789, "type": "private"},
            "from": {"id": 123456789},
            "text": "/status",
        },
    }

    with patch("matrix_ai.telegram.TelegramChatBridge._send", new_callable=AsyncMock) as mock_send:
        result = await run_cloud_step(
            config=config,
            repo_dir=tmp_path,
            pull_state_enabled=False,
            push_state_enabled=False,
            sync_telegram_enabled=True,
            incoming_update=update_payload,
            webhook_secret="sec-123",
            drain_timeout=1.0,
        )

        assert result.telegram_updates_processed == 1
        assert result.error is None
        assert mock_send.await_count >= 1


@pytest.mark.asyncio
async def test_run_cloud_step_webhook_mode_scheduled_cron_does_not_poll(tmp_path: Path):
    db_path = tmp_path / "matrix.db"
    config = MatrixConfig(
        home=tmp_path,
        db_path=db_path,
        model_provider="none",
        telegram_bot_token="test-token",
        telegram_chat_id="123456789",
        telegram_mode="webhook",
    )

    with patch("matrix_ai.telegram.TelegramChatBridge._api", new_callable=AsyncMock) as mock_api:
        result = await run_cloud_step(
            config=config,
            repo_dir=tmp_path,
            pull_state_enabled=False,
            push_state_enabled=False,
            sync_telegram_enabled=True,
            incoming_update=None,  # Scheduled cron run without incoming webhook
            drain_timeout=1.0,
        )

        assert result.telegram_updates_processed == 0
        assert result.error is None
        # getUpdates must NEVER be called in webhook mode during scheduled cron runs
        for call_args in mock_api.call_args_list:
            assert call_args[0][0] != "getUpdates"


def test_cli_cloud_step_with_ingest_update_and_dispatch_payload(tmp_path: Path, monkeypatch):
    import json

    from matrix_ai.cli import main as cli_main

    monkeypatch.setenv("MATRIX_HOME", str(tmp_path))
    monkeypatch.setenv("MATRIX_TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("MATRIX_TELEGRAM_CHAT_ID", "123456789")
    monkeypatch.setenv("MATRIX_TELEGRAM_MODE", "webhook")
    monkeypatch.setenv("MATRIX_TELEGRAM_WEBHOOK_SECRET", "wh-secret")

    called_kwargs = {}

    async def fake_run_cloud_step(**kwargs):
        called_kwargs.update(kwargs)
        return CloudStepResult(
            started_at="2026-11-14T12:00:00Z",
            finished_at="2026-11-14T12:00:01Z",
            duration_seconds=1.0,
            recovered_tasks=0,
            telegram_updates_processed=1,
            tasks_completed=0,
            reports_flushed=0,
            state_pulled=False,
            state_pushed=False,
            primary_provider="none",
            primary_status="mocked",
            fallback_provider="none",
            fallback_status="mocked",
            active_tasks_remaining=0,
            db_integrity="ok",
        )

    monkeypatch.setattr("matrix_ai.cloud_native.run_cloud_step", fake_run_cloud_step)

    # 1. Via --ingest-update flag
    payload_json = json.dumps({"update_id": 888, "update": {"update_id": 888, "message": {"text": "hello"}}})
    exit_code = cli_main(["cloud-step", "--ingest-update", payload_json, "--no-pull-state", "--no-push-state"])
    assert exit_code == 0
    assert called_kwargs["incoming_update"] == {"update_id": 888, "message": {"text": "hello"}}
    assert called_kwargs["webhook_secret"] == "wh-secret"

    # 2. Via DISPATCH_PAYLOAD environment variable
    monkeypatch.setenv("DISPATCH_PAYLOAD", json.dumps({"update_id": 777, "message": {"text": "from env"}}))
    exit_code = cli_main(["cloud-step", "--no-pull-state", "--no-push-state"])
    assert exit_code == 0
    assert called_kwargs["incoming_update"] == {"update_id": 777, "message": {"text": "from env"}}


def test_cloudflare_worker_security_contract():
    worker_path = Path(__file__).resolve().parent.parent / "deploy" / "serverless" / "cloudflare_telegram_webhook_worker.js"
    assert worker_path.exists()
    content = worker_path.read_text(encoding="utf-8")

    # 1. Mandatory secrets (fail-closed if missing)
    assert "TELEGRAM_SECRET_TOKEN" in content
    assert "TELEGRAM_OWNER_ID" in content
    assert "GITHUB_TOKEN" in content
    assert "!secretTokenConfig || !ownerIdConfig || !githubToken" in content
    assert "status: 500" in content

    # 2. Secret token header verification
    assert "X-Telegram-Bot-Api-Secret-Token" in content
    assert "status: 401" in content

    # 3. Payload validation
    assert "Number.isInteger(payload.update_id)" in content
    assert "status: 400" in content

    # 4. Replay deduplication
    assert "recentUpdateIds" in content
    assert "already_processed" in content

    # 5. Strict sender & chat authentication
    assert 'chatType !== "private"' in content
    assert "status: 403" in content

    # 6. GitHub dispatch status check
    assert "dispatchResp.status !== 204" in content
    assert "status: 502" in content

    # 7. Zero logging of secrets or message text
    assert "console.log(secret" not in content
    assert "console.log(payload" not in content
    assert "console.log(message.text" not in content

