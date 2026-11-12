"""A17 - file tools reject traversal and paths outside approved roots."""

from __future__ import annotations

import pytest
from conftest import create_workspace_file, execute_tool, wait_for_task

pytestmark = pytest.mark.acceptance


async def test_parent_traversal_is_refused(client, runtime):
    outside = runtime.config.home.parent / "outside_secret.txt"
    outside.write_text("TOP SECRET OUTSIDE THE WORKSPACE", encoding="utf-8")

    task = await execute_tool(client, "files.read_text", {"path": "../outside_secret.txt"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "outside" in (done["error"] or "").lower()
    assert "TOP SECRET" not in str(done["tool_calls"])


async def test_absolute_path_outside_roots_is_refused(client, runtime):
    target = runtime.config.home.parent / "absolute_outside.txt"
    target.write_text("outside", encoding="utf-8")
    task = await execute_tool(client, "files.read_text", {"path": str(target)})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert "outside" in (done["error"] or "").lower()


async def test_write_outside_roots_is_refused(client, runtime):
    outside = runtime.config.home.parent / "pwned.txt"
    task = await execute_tool(
        client, "files.write_text", {"path": "../pwned.txt", "content": "pwned"}
    )
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "FAILED"
    assert not outside.exists()


async def test_inside_workspace_paths_still_work(client, runtime):
    created = await create_workspace_file(client, "safe.txt", "safe content")
    assert created["state"] == "COMPLETE"
    assert (runtime.config.workspace_dir / "safe.txt").read_text() == "safe content"

    task = await execute_tool(client, "files.read_text", {"path": "safe.txt"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE"
    assert done["tool_calls"][-1]["output"]["content"] == "safe content"
