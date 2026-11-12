"""Minimal MCP stdio client for owner-configured local add-ons.

The owner supplies executable and arguments through MATRIX_ADDON_MCP_SERVERS.
No model output is ever interpreted as a process command or argument.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import Mapping
from typing import Any

from ..errors import ToolExecutionError, ToolUnavailable

log = logging.getLogger("matrix.addons.mcp")
_MAX_LINE_BYTES = 2_000_000
_MAX_OUTPUT_CHARS = 80_000


def _safe_env(extra: Mapping[str, Any]) -> dict[str, str]:
    """Build a minimal child environment; never inherit arbitrary parent secrets."""
    keys = ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE", "TEMP", "TMP", "APPDATA", "LOCALAPPDATA")
    result = {key: os.environ[key] for key in keys if os.environ.get(key)}
    for key, value in extra.items():
        if isinstance(key, str) and isinstance(value, str) and key.isidentifier() and len(key) <= 80:
            result[key] = value[:8000]
    return result


class MCPStdioClient:
    def __init__(
        self,
        *,
        command: str,
        args: list[Any],
        env: dict[str, Any],
        server_name: str,
        cwd: str | None = None,
        request_timeout: float = 120.0,
    ) -> None:
        if not command or not isinstance(command, str) or len(command) > 300:
            raise ToolUnavailable("MCP server command is not configured")
        if not isinstance(args, list) or len(args) > 64 or any(not isinstance(arg, str) or len(arg) > 2000 for arg in args):
            raise ToolUnavailable("MCP server arguments must be a bounded list of strings")
        if not isinstance(env, dict) or len(env) > 64:
            raise ToolUnavailable("MCP server environment must be a bounded object")
        self.command = command
        self.args = list(args)
        self.env = _safe_env(env)
        self.server_name = server_name
        self.cwd = cwd
        self.request_timeout = max(5.0, min(float(request_timeout), 300.0))
        self.process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 1
        self._write_lock = asyncio.Lock()
        self._closing = False
        self._tools: list[dict[str, Any]] = []
        self.stderr_tail = ""

    async def connect(self) -> list[dict[str, Any]]:
        try:
            self.process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.cwd,
                env=self.env,
            )
        except (OSError, ValueError) as exc:
            raise ToolUnavailable(f"Could not start configured MCP server '{self.server_name}': {type(exc).__name__}") from exc
        self._reader_task = asyncio.create_task(self._read_stdout(), name=f"matrix-mcp-{self.server_name}")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name=f"matrix-mcp-stderr-{self.server_name}")
        try:
            await self.request(
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "MATRIX", "version": "0.1.0"},
                },
            )
            await self.notify("notifications/initialized", {})
            result = await self.request("tools/list", {})
            tools = result.get("tools", []) if isinstance(result, dict) else []
            if not isinstance(tools, list):
                raise ToolUnavailable(f"MCP server '{self.server_name}' returned an invalid tools catalogue")
            self._tools = [self._validate_tool(item) for item in tools[:256] if isinstance(item, dict)]
            if not self._tools:
                raise ToolUnavailable(f"MCP server '{self.server_name}' exposes no callable tools")
            return list(self._tools)
        except Exception:
            await self.close()
            raise

    @staticmethod
    def _validate_tool(raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("name")
        schema = raw.get("inputSchema") or raw.get("input_schema") or {"type": "object", "properties": {}}
        if not isinstance(name, str) or not name or len(name) > 160:
            raise ToolUnavailable("MCP tool catalogue contains an invalid tool name")
        if not isinstance(schema, dict) or schema.get("type", "object") != "object":
            raise ToolUnavailable(f"MCP tool '{name}' has an unsupported input schema")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict) or len(properties) > 64:
            raise ToolUnavailable(f"MCP tool '{name}' has an invalid input schema")
        return {
            "name": name,
            "description": str(raw.get("description") or name)[:1000],
            "inputSchema": schema,
            "annotations": raw.get("annotations") if isinstance(raw.get("annotations"), dict) else {},
        }

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise ToolUnavailable(f"MCP server '{self.server_name}' is not running")
        request_id = self._next_id
        self._next_id += 1
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        message = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            async with self._write_lock:
                process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
                await process.stdin.drain()
            response = await asyncio.wait_for(future, timeout=self.request_timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise ToolUnavailable(f"MCP server '{self.server_name}' timed out on {method}") from exc
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise ToolUnavailable(f"MCP server '{self.server_name}' closed its input stream") from exc
        if not isinstance(response, dict):
            raise ToolUnavailable(f"MCP server '{self.server_name}' returned a malformed response")
        if "error" in response:
            error = response.get("error") or {}
            message_text = str(error.get("message") or "MCP request failed")[:500]
            raise ToolExecutionError(f"MCP {method} failed: {message_text}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise ToolUnavailable(f"MCP server '{self.server_name}' returned no result for {method}")
        return result

    async def notify(self, method: str, params: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            raise ToolUnavailable(f"MCP server '{self.server_name}' is not running")
        payload = {"jsonrpc": "2.0", "method": method, "params": params}
        async with self._write_lock:
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while not self._closing:
                line = await self.process.stdout.readline()
                if not line:
                    break
                if len(line) > _MAX_LINE_BYTES:
                    raise ToolUnavailable(f"MCP server '{self.server_name}' exceeded the response limit")
                try:
                    payload = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    # MCP stdout must be protocol-only; ignore and redact malformed output.
                    log.warning("non-protocol stdout from MCP server %s", self.server_name)
                    continue
                if not isinstance(payload, dict):
                    continue
                response_id = payload.get("id")
                if isinstance(response_id, int):
                    future = self._pending.pop(response_id, None)
                    if future is not None and not future.done():
                        future.set_result(payload)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("MCP stdout reader stopped for %s: %s", self.server_name, type(exc).__name__)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ToolUnavailable(f"MCP server '{self.server_name}' stopped"))
            self._pending.clear()

    async def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        try:
            while not self._closing:
                chunk = await self.process.stderr.read(2048)
                if not chunk:
                    return
                text = chunk.decode("utf-8", errors="replace")
                self.stderr_tail = (self.stderr_tail + text)[-_MAX_OUTPUT_CHARS:]
        except asyncio.CancelledError:
            raise

    async def list_tools(self) -> list[dict[str, Any]]:
        result = await self.request("tools/list", {})
        tools = result.get("tools", [])
        return [self._validate_tool(item) for item in tools if isinstance(item, dict)][:256]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.request(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        if result.get("isError") is True:
            contents = result.get("content", [])
            text = " ".join(
                str(item.get("text", "")) for item in contents
                if isinstance(item, dict) and item.get("type") == "text"
            )
            raise ToolExecutionError(f"MCP tool '{name}' reported an error: {text[:500]}")
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            output: dict[str, Any] = structured
        else:
            content = result.get("content", [])
            excerpts = []
            for item in content[:30] if isinstance(content, list) else []:
                if isinstance(item, dict) and item.get("type") == "text":
                    excerpts.append(str(item.get("text") or "")[:12000])
            output = {"content": excerpts}
        encoded = json.dumps(output, ensure_ascii=False, default=str)
        if len(encoded) > _MAX_OUTPUT_CHARS:
            output = {"content": encoded[:_MAX_OUTPUT_CHARS], "truncated": True}
        output["_untrusted_mcp_result"] = True
        output["_mcp_server"] = self.server_name
        return output

    async def close(self) -> None:
        self._closing = True
        process = self.process
        if process is None:
            return
        if process.stdin and not process.stdin.is_closing():
            process.stdin.close()
        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=3.0)
            except (ProcessLookupError, TimeoutError):
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), timeout=3.0)
                except TimeoutError:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task and not task.done():
                task.cancel()
        tasks = [task for task in (self._reader_task, self._stderr_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.process = None
        self._reader_task = None
        self._stderr_task = None


__all__ = ["MCPStdioClient"]
