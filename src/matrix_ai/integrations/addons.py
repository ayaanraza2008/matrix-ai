"""Lazy, pinned add-on repository manager and context router.

Sources remain outside the MATRIX git tree, are cloned at immutable commits,
and never execute package scripts during source installation. Skills are
retrieved as bounded, explicitly untrusted reference context.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from .addon_catalog import ADDONS, ADDON_BY_ID, addon_public_view

log = logging.getLogger("matrix.addons")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+_.-]{1,50}", re.I)
_STOP = {
    "the", "and", "for", "with", "from", "into", "this", "that", "make",
    "create", "please", "help", "use", "matrix", "can", "you", "any", "how",
}
_BLOCKED_CONTEXT = {"g0dm0d3", "hexstrike-ai", "harness2"}
_MAX_DOC_BYTES = 60_000


class AddonError(RuntimeError):
    pass


def _tokens(text: str) -> set[str]:
    return {word.casefold() for word in _TOKEN.findall(text or "") if len(word) > 2 and word.casefold() not in _STOP}


class AddonManager:
    def __init__(self, config, *, store=None, mcp_servers: dict[str, Any] | None = None) -> None:
        self.config = config
        self.store = store
        self.root = (Path(config.home) / "addons").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.mcp_servers = mcp_servers if mcp_servers is not None else config.addon_mcp_servers
        self._connections: dict[str, Any] = {}
        self._connection_errors: dict[str, str] = {}

    def _path(self, addon_id: str) -> Path:
        if addon_id not in ADDON_BY_ID:
            raise AddonError(f"unknown add-on: {addon_id}")
        return (self.root / addon_id).resolve()

    def _marker(self, addon_id: str) -> Path:
        return self._path(addon_id) / ".matrix-addon.json"

    def _installed_info(self, addon_id: str) -> dict[str, Any] | None:
        marker = self._marker(addon_id)
        if not marker.is_file():
            return None
        try:
            value = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        addon = ADDON_BY_ID[addon_id]
        if value.get("repository") != addon["repository"] or value.get("commit") != addon["commit"]:
            return None
        return value

    def status(self) -> dict[str, Any]:
        items = []
        for addon in ADDONS:
            addon_id = addon["id"]
            installed_info = self._installed_info(addon_id)
            items.append(
                {
                    **addon_public_view(
                        addon,
                        installed=installed_info is not None,
                        connected=addon_id in self._connections,
                    ),
                    "installed_commit": installed_info.get("commit") if installed_info else None,
                    "last_install": installed_info.get("installed_at") if installed_info else None,
                    "connection_error": self._connection_errors.get(addon_id),
                    "mcp_configured": addon_id in self.mcp_servers,
                }
            )
        return {
            "count": len(items),
            "installed_count": sum(item["installed"] for item in items),
            "connected_count": sum(item["connected"] for item in items),
            "mcp_configured_count": sum(item["mcp_configured"] for item in items),
            "addons": items,
        }

    def get(self, addon_id: str) -> dict[str, Any] | None:
        if addon_id not in ADDON_BY_ID:
            return None
        result = next(item for item in self.status()["addons"] if item["id"] == addon_id)
        return result

    def route(self, query: str, *, limit: int = 5) -> list[dict[str, Any]]:
        terms = _tokens(query)
        if not terms:
            return []
        ranked: list[tuple[float, str, dict[str, Any]]] = []
        for addon in ADDONS:
            if addon["id"] in _BLOCKED_CONTEXT and not any(
                word in (query or "").casefold() for word in ("red team", "pentest", "security lab", "benchmark")
            ):
                continue
            haystack = " ".join(
                [addon["name"], addon["kind"], addon["protocol"], *addon["capabilities"], *addon["keywords"]]
            ).casefold()
            matched = sorted(term for term in terms if term in haystack)
            score = len(matched) / max(1, len(terms))
            # Direct keyword matches receive a modest boost, but never make a
            # source executable or change its risk classification.
            for keyword in addon["keywords"]:
                if len(keyword) > 3 and keyword.casefold() in (query or "").casefold():
                    score += 0.18
            if score >= 0.12:
                installed = self._installed_info(addon["id"])
                ranked.append(
                    (
                        score,
                        addon["id"],
                        {
                            **addon_public_view(
                                addon,
                                installed=installed is not None,
                                connected=addon["id"] in self._connections,
                            ),
                            "matched_terms": matched[:12],
                            "selection_reason": "Capability and task-keyword match; source remains untrusted.",
                        },
                    )
                )
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[: max(1, min(int(limit), 10))]]

    def install(self, addon_id: str, *, allow_large: bool = False) -> dict[str, Any]:
        addon = ADDON_BY_ID.get(addon_id)
        if addon is None:
            raise AddonError(f"unknown add-on: {addon_id}")
        if addon.get("license") == "UNKNOWN":
            raise AddonError(
                f"{addon_id} has no recognized repository licence; MATRIX keeps it as a link-only catalogue"
            )
        existing = self._installed_info(addon_id)
        if existing:
            return {"addon_id": addon_id, "status": "already_installed", **existing}
        if int(addon["size_mb"]) > 200 and not allow_large:
            raise AddonError(
                f"{addon_id} is estimated at {addon['size_mb']} MB; repeat the owner install with allow_large=true"
            )
        destination = self._path(addon_id)
        if destination.exists() and any(destination.iterdir()):
            raise AddonError(f"refusing to overwrite non-managed add-on directory: {destination.name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        repo_url = f"https://github.com/{addon['repository']}.git"
        patterns = list(addon["sparse_paths"])
        try:
            if not destination.exists():
                destination.mkdir(parents=True)
            commands = [
                ["git", "init", str(destination)],
                ["git", "-C", str(destination), "remote", "add", "origin", repo_url],
                ["git", "-C", str(destination), "config", "core.sparseCheckout", "true"],
            ]
            # Git's sparse-checkout file is written by MATRIX, then only the
            # pinned commit and selected paths are fetched and checked out.
            sparse_file = destination / ".git" / "info" / "sparse-checkout"
            sparse_file.parent.mkdir(parents=True, exist_ok=True)
            sparse_file.write_text(
                "\n".join(patterns) + "\n",
                encoding="utf-8",
            )
            for command in commands:
                self._run_git(command)
            self._run_git(
                ["git", "-C", str(destination), "fetch", "--depth", "1", "--filter=blob:none", "origin", addon["commit"]]
            )
            self._run_git(["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"])
            from datetime import UTC, datetime

            marker = {
                "addon_id": addon_id,
                "repository": addon["repository"],
                "commit": addon["commit"],
                "license": addon["license"],
                "installed_at": datetime.now(UTC).isoformat(),
                "source_only": True,
                "dependencies_installed": False,
                "code_executed": False,
            }
            (destination / ".matrix-addon.json").write_text(json.dumps(marker, indent=2), encoding="utf-8")
            return {"addon_id": addon_id, "status": "installed_source_only", **marker}
        except Exception as exc:
            # Keep only a valid manager-created directory; remove incomplete clones.
            import shutil

            shutil.rmtree(destination, ignore_errors=True)
            raise AddonError(f"source install failed for {addon_id}: {type(exc).__name__}: {exc}") from exc

    @staticmethod
    def _run_git(command: list[str]) -> None:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=300,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AddonError(f"git operation failed: {type(exc).__name__}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "git returned a non-zero exit code")[-1200:]
            raise AddonError(detail)

    def search_context(
        self, query: str, *, max_docs: int = 3, max_total_chars: int = 5000
    ) -> tuple[list[dict[str, Any]], str]:
        """Search only installed, approved-to-context text files; return bounded untrusted context."""
        candidates = self.route(query, limit=6)
        selected: list[dict[str, Any]] = []
        context_parts: list[str] = []
        remaining = max(0, min(int(max_total_chars), 9000))
        for item in candidates:
            addon_id = item["id"]
            addon = ADDON_BY_ID[addon_id]
            if not addon["context_enabled"] or addon_id in _BLOCKED_CONTEXT:
                continue
            root = self._path(addon_id)
            if not self._installed_info(addon_id) or not root.is_dir():
                continue
            docs = self._matching_docs(root, addon, query)
            for score, path, content in docs:
                if len(selected) >= max_docs or remaining <= 0:
                    break
                excerpt = content[: min(1600, remaining)]
                if len(excerpt.strip()) < 80:
                    continue
                selected.append(
                    {
                        "addon_id": addon_id,
                        "name": addon["name"],
                        "path": path,
                        "score": round(score, 4),
                        "characters": len(excerpt),
                    }
                )
                block = (
                    f"[UNTRUSTED ADD-ON REFERENCE: {addon['name']} / {path}]\n"
                    + excerpt
                    + "\n[END UNTRUSTED ADD-ON REFERENCE]\n"
                )
                context_parts.append(block)
                remaining -= len(block)
        return selected, "\n".join(context_parts)

    def _matching_docs(self, root: Path, addon: dict[str, Any], query: str) -> list[tuple[float, str, str]]:
        terms = _tokens(query)
        if not terms:
            return []
        roots = [root / part.rstrip("/*") for part in addon.get("context_paths", [])]
        paths: list[Path] = []
        for subroot in roots:
            if subroot.is_file():
                paths.append(subroot)
            elif subroot.is_dir():
                paths.extend(subroot.rglob("*.md"))
                paths.extend(subroot.rglob("*.mdx"))
        output: list[tuple[float, str, str]] = []
        seen: set[Path] = set()
        for path in paths:
            try:
                resolved = path.resolve()
                resolved.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            if resolved in seen or not resolved.is_file() or resolved.name.upper().startswith("LICENSE"):
                continue
            seen.add(resolved)
            try:
                if resolved.stat().st_size > _MAX_DOC_BYTES:
                    continue
                content = resolved.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if addon["id"] == "anthropic-cybersecurity-skills":
                relative_path = resolved.relative_to(root).as_posix().casefold()
                standards_reference = "/references/standards.md" in relative_path
                allowed_document = (
                    relative_path == "readme.md"
                    or relative_path.startswith("mappings/")
                    or relative_path == "docs/mitre-f3-mapping.md"
                    or standards_reference
                )
                if not allowed_document:
                    continue
            haystack = (resolved.name + " " + content[:6000]).casefold()
            matches = sum(1 for term in terms if term in haystack)
            score = matches / max(1, len(terms))
            if score >= 0.15:
                output.append((score, resolved.relative_to(root).as_posix(), content))
        output.sort(key=lambda item: (-item[0], item[1]))
        return output[:10]

    async def prepare_selected(self, selected: list[dict[str, Any]], registry) -> None:
        """Fetch safe selected source references and start explicitly configured local services."""
        for selected_addon in selected:
            addon_id = str(selected_addon.get("id") or "")
            addon = ADDON_BY_ID.get(addon_id)
            if addon is None:
                continue

            # Only context-safe small source repositories are lazily fetched.
            # This does not install dependencies or run any third-party scripts.
            if (
                self.config.addon_auto_install_sources
                and addon["context_enabled"]
                and addon_id not in _BLOCKED_CONTEXT
                and int(addon.get("size_mb", 0)) <= 200
                and self._installed_info(addon_id) is None
            ):
                try:
                    result = await asyncio.to_thread(
                        self.install, addon_id, allow_large=False
                    )
                    if self.store is not None:
                        self.store.audit(
                            "addon.source_auto_installed",
                            actor="addon-router",
                            status="installed",
                            detail={
                                "addon_id": addon_id,
                                "commit": result.get("commit"),
                                "source_only": True,
                            },
                        )
                except Exception as exc:  # optional source fetch must never block the task
                    self._connection_errors[addon_id] = f"source_install:{type(exc).__name__}"
                    log.warning(
                        "lazy add-on source install failed for %s: %s",
                        addon_id,
                        type(exc).__name__,
                    )

            # Owner configuration is the explicit opt-in to execute a local
            # server command or send an API bearer key. Offensive security
            # and unrestricted evaluation engines are never auto-started.
            if (
                addon_id in self.mcp_servers
                and addon_id not in self._connections
                and addon["protocol"] in {"mcp_stdio", "mcp_candidate", "desktop_mcp"}
            ):
                try:
                    await self.connect(addon_id, registry)
                except Exception as exc:  # service dependencies may not be available
                    self._connection_errors[addon_id] = f"auto_connect:{type(exc).__name__}"
                    log.warning(
                        "configured MCP auto-connect failed for %s: %s",
                        addon_id,
                        type(exc).__name__,
                    )

    async def connect(self, addon_id: str, registry) -> dict[str, Any]:
        """Connect only a preconfigured, owner-approved stdio MCP server."""
        addon = ADDON_BY_ID.get(addon_id)
        if addon is None:
            raise AddonError(f"unknown add-on: {addon_id}")
        if addon["protocol"] not in {"mcp_stdio", "mcp_candidate", "desktop_mcp", "security_mcp_bridge"}:
            raise AddonError(f"{addon_id} is not a direct stdio MCP connector")
        spec = self.mcp_servers.get(addon_id)
        if not isinstance(spec, dict):
            raise AddonError(f"{addon_id} has no MATRIX_ADDON_MCP_SERVERS configuration")
        from .mcp_addons import MCPStdioClient
        from ..tools.external_mcp import build_mcp_tools

        existing = self._connections.get(addon_id)
        if existing:
            await self.disconnect(addon_id, registry)
        client = MCPStdioClient(
            command=str(spec.get("command") or ""),
            args=spec.get("args") if isinstance(spec.get("args"), list) else [],
            env=spec.get("env") if isinstance(spec.get("env"), dict) else {},
            server_name=addon_id,
            cwd=str(self._path(addon_id)) if self._installed_info(addon_id) else None,
            transport=str(spec.get("transport") or "stdio"),
            url=str(spec.get("url") or ""),
            headers=spec.get("headers") if isinstance(spec.get("headers"), dict) else {},
        )
        tools = await client.connect()
        try:
            created = build_mcp_tools(
                addon_id, tools, client,
                read_only_tools=set(spec.get("read_only_tools", [])),
                target_allowlist=set(self.config.hexstrike_allowed_targets),
            )
        except Exception:
            await client.close()
            raise
        if not created:
            await client.close()
            raise AddonError(f"{addon_id} MCP server exposed no tools")
        registered: list[str] = []
        try:
            for tool in created:
                registry.register(tool)
                registered.append(tool.name)
        except Exception:
            for tool_name in registered:
                registry.unregister(tool_name)
            await client.close()
            raise
        self._connections[addon_id] = {"client": client, "tools": [tool.name for tool in created]}
        self._connection_errors.pop(addon_id, None)
        if self.store is not None:
            self.store.audit(
                "addon.mcp_connected", actor="addon-manager", status="connected",
                detail={"addon_id": addon_id, "tool_count": len(created)},
            )
        return {"addon_id": addon_id, "status": "connected", "tool_count": len(created), "tools": [tool.name for tool in created]}

    async def disconnect(self, addon_id: str, registry) -> dict[str, Any]:
        connection = self._connections.pop(addon_id, None)
        if not connection:
            return {"addon_id": addon_id, "status": "not_connected"}
        for tool_name in connection["tools"]:
            registry.unregister(tool_name)
        await connection["client"].close()
        if self.store is not None:
            self.store.audit(
                "addon.mcp_disconnected", actor="addon-manager", status="disconnected",
                detail={"addon_id": addon_id},
            )
        return {"addon_id": addon_id, "status": "disconnected"}

    async def disconnect_all(self, registry) -> None:
        for addon_id in list(self._connections):
            try:
                await self.disconnect(addon_id, registry)
            except Exception as exc:  # noqa: BLE001 - shutdown must finish
                log.warning("MCP add-on shutdown failed for %s: %s", addon_id, type(exc).__name__)


__all__ = ["AddonError", "AddonManager"]
