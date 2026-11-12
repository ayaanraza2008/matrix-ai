"""Shared test fixtures: hermetic runtime, HTTP clients and task helpers.

No network, no real credentials, no camera/microphone required.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from matrix_ai.app import create_app
from matrix_ai.config import MatrixConfig
from matrix_ai.runtime import MatrixRuntime

TEST_TOKEN = "matrix-test-owner-token-1234567890"
TERMINAL_STATES = {"COMPLETE", "PARTIAL", "FAILED", "CANCELLED"}


@pytest.fixture
def config(tmp_path: Path) -> MatrixConfig:
    return MatrixConfig(
        home=tmp_path / "home",
        owner_token=TEST_TOKEN,
        model_provider="none",
        addon_auto_install_sources=False,  # tests never fetch third-party sources
        rate_limit_per_minute=100_000,
        approval_wait_seconds=3.0,
        approval_ttl_seconds=120,
        backoff_base_seconds=0.01,
        task_deadline_seconds=60.0,
    )


@pytest_asyncio.fixture
async def runtime(config: MatrixConfig):
    instance = MatrixRuntime.build(config)
    try:
        yield instance
    finally:
        await instance.shutdown()


@pytest_asyncio.fixture
async def app(runtime: MatrixRuntime):
    return create_app(runtime=runtime, start_background=False)


@pytest_asyncio.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://matrix.test",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    ) as http:
        yield http


@pytest_asyncio.fixture
async def anon_client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://matrix.test") as http:
        yield http


async def wait_for_task(client: AsyncClient, task_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] in TERMINAL_STATES:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} did not finish; last state={last.get('state')}")


async def wait_for_state(
    client: AsyncClient, task_id: str, states: set[str], timeout: float = 8.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = await client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["state"] in states:
            return last
        await asyncio.sleep(0.05)
    raise AssertionError(f"task {task_id} never reached {states}; last state={last.get('state')}")


async def execute_tool(client: AsyncClient, tool_name: str, arguments: dict, **extra) -> dict:
    payload = {"arguments": arguments, **extra}
    response = await client.post(f"/api/tools/{tool_name}/execute", json=payload)
    assert response.status_code == 202, response.text
    return response.json()


async def create_workspace_file(client: AsyncClient, name: str, content: str) -> dict:
    task = await execute_tool(client, "files.write_text", {"path": name, "content": content})
    return await wait_for_task(client, task["id"])
