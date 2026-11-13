"""Autonomous, policy-isolated integration for the Vercel Skills CLI.

MATRIX discovers skills through the public skills.sh search API and installs
pinned CLI releases into its private data directory. Only bounded SKILL.md
instruction files are loaded as untrusted planner context. No skill scripts,
hooks, package managers, or commands are executed.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger("matrix.skills")

SKILLS_CLI_VERSION = "1.8.1"
SKILLS_SEARCH_URL = "https://skills.sh/api/search"
_NODE_MINIMUM = (22, 20, 0)
_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$", re.I)
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+_.-]{1,50}", re.I)
_STOP = {"the", "and", "for", "with", "from", "into", "this", "that", "make",
         "create", "please", "help", "use", "matrix", "can", "you", "any", "how",
         "what", "want", "need", "about", "task"}
_MAX_SKILLS = 3
_MAX_SKILL_CHARS = 12_000


class SkillsError(RuntimeError):
    """A bounded, user-safe Skills integration error."""


def _tokens(value: str) -> set[str]:
    return {item.casefold() for item in _TOKEN.findall(value or "")
            if len(item) > 2 and item.casefold() not in _STOP}


def _frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---\n"):
        return "", ""
    end = content.find("\n---", 4)
    if end < 0:
        return "", ""
    header = content[4:end]
    name_match = re.search(r"(?m)^name:\s*[\"']?([^\"'\r\n#]+)", header)
    desc_match = re.search(r"(?m)^description:\s*[\"']?([^\"'\r\n#]+)", header)
    name = name_match.group(1).strip() if name_match else ""
    description = desc_match.group(1).strip() if desc_match else ""
    return name[:80], description[:500]


class SkillsManager:
    """Search, install, route and load skills without granting them tool authority."""

    def __init__(self, config, *, store=None, http_client_factory=None) -> None:
        self.config = config
        self.store = store
        self.root = (Path(config.home) / "skills").resolve()
        self.project = self.root / "cli-project"
        self.skills_dir = self.project / ".agents" / "skills"
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self.auto_discover = os.environ.get("MATRIX_SKILLS_AUTO_DISCOVER", "true").casefold() not in {"0", "false", "no"}
        self.auto_install = os.environ.get("MATRIX_SKILLS_AUTO_INSTALL", "true").casefold() not in {"0", "false", "no"}
        self.auto_update = os.environ.get("MATRIX_SKILLS_AUTO_UPDATE", "false").casefold() in {"1", "true", "yes"}
        self._http_client_factory = http_client_factory or httpx.AsyncClient
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        installed = self.list_installed()
        return {
            "enabled": True,
            "cli": "skills",
            "cli_version": SKILLS_CLI_VERSION,
            "root": str(self.root),
            "auto_discover": self.auto_discover,
            "auto_install": self.auto_install,
            "auto_update": self.auto_update,
            "installed_count": len(installed),
            "installed": installed,
            "last_error": self._last_error,
            "execution_policy": "SKILL.md context only; no skill code or hooks are executed",
        }

    def list_installed(self) -> list[dict[str, str]]:
        items: list[dict[str, str]] = []
        if not self.skills_dir.is_dir():
            return items
        for path in sorted(self.skills_dir.iterdir(), key=lambda item: item.name.casefold()):
            if not _NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            skill_file = path / "SKILL.md"
            if skill_file.is_symlink() or not skill_file.is_file():
                continue
            try:
                if skill_file.stat().st_size > 64_000:
                    continue
                content = skill_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            name, description = _frontmatter(content)
            if name:
                items.append({"name": name, "description": description, "directory": path.name})
        return items[:500]

    async def search(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        query = " ".join((query or "").split())[:120]
        if len(query) < 2:
            return []
        try:
            async with self._http_client_factory(timeout=8.0, follow_redirects=False) as client:
                response = await client.get(SKILLS_SEARCH_URL, params={"q": query})
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError, OSError) as exc:
            self._last_error = f"search:{type(exc).__name__}"
            return []
        raw = payload.get("skills", []) if isinstance(payload, dict) else []
        results: list[dict[str, Any]] = []
        for item in raw[: max(1, min(int(limit), 20))]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            source = str(item.get("source") or "").strip()
            slug = str(item.get("id") or "").strip()
            if not _NAME.fullmatch(name) or not _REPO.fullmatch(source):
                continue
            results.append({
                "name": name,
                "source": source,
                "slug": slug[:200],
                "installs": max(0, int(item.get("installs") or 0)),
            })
        return results

    @staticmethod
    def _node_ready() -> bool:
        node = shutil.which("node")
        if not node:
            return False
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True,
                                    timeout=5, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            return False
        match = re.search(r"v?(\d+)\.(\d+)\.(\d+)", result.stdout or "")
        return bool(match and tuple(map(int, match.groups())) >= _NODE_MINIMUM)

    def install(self, source: str, skill_name: str) -> dict[str, Any]:
        if not _REPO.fullmatch(source or "") or not _NAME.fullmatch(skill_name or ""):
            raise SkillsError("Only validated public owner/repository and skill names can be installed.")
        if not self._node_ready():
            raise SkillsError("Vercel Skills requires Node.js 22.20.0 or newer; no compatible Node runtime was found.")
        node = shutil.which("node")
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not node or not npx:
            raise SkillsError("node and npx were not found; install Node.js 22.20.0 or newer.")
        # Windows npm launchers are .cmd files and cannot be started with
        # shell=False. Invoke npm's bundled npx-cli.js through node instead.
        npx_cli = Path(node).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        launcher = [node, str(npx_cli)] if os.name == "nt" and npx_cli.is_file() else [npx]
        destination = self.skills_dir / skill_name
        if destination.exists():
            return {"status": "already_installed", "name": skill_name, "source": source}
        self.project.mkdir(parents=True, exist_ok=True)
        command = [
            *launcher, "--yes", f"skills@{SKILLS_CLI_VERSION}", "add", source,
            "--skill", skill_name, "--agent", "universal", "--copy", "--yes", "--json",
        ]
        # No shell, no model-controlled flags, no inherited secret-bearing environment.
        allowed = {"PATH", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMROOT",
                   "TEMP", "TMP", "NPM_CONFIG_CACHE", "NPM_CONFIG_USERCONFIG"}
        env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        env.update({"CI": "1", "NO_COLOR": "1", "npm_config_audit": "false",
                    "npm_config_fund": "false"})
        try:
            result = subprocess.run(command, cwd=self.project, env=env, capture_output=True,
                                    text=True, timeout=240, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SkillsError(f"Skills CLI could not complete: {type(exc).__name__}") from exc
        if result.returncode != 0 or not (destination / "SKILL.md").is_file():
            detail = (result.stderr or result.stdout or "CLI did not produce a valid SKILL.md")[-800:]
            raise SkillsError(f"Skill installation failed: {detail}")
        content = (destination / "SKILL.md").read_text(encoding="utf-8", errors="replace")
        declared_name, _ = _frontmatter(content)
        if declared_name.casefold() != skill_name.casefold():
            shutil.rmtree(destination, ignore_errors=True)
            raise SkillsError("Installed SKILL.md name did not match the requested skill; installation was rolled back.")
        self._audit("skills.installed", {"source": source, "name": skill_name,
                                        "cli_version": SKILLS_CLI_VERSION})
        return {"status": "installed", "name": skill_name, "source": source,
                "cli_version": SKILLS_CLI_VERSION, "code_executed": False}

    def update(self) -> dict[str, Any]:
        """Update the installed skill lock through the pinned official CLI."""
        if not self.list_installed():
            return {"status": "empty", "updated": 0}
        if not self._node_ready():
            raise SkillsError("Node.js 22.20.0 or newer is required to update skills.")
        node = shutil.which("node")
        npx = shutil.which("npx") or shutil.which("npx.cmd")
        if not node or not npx:
            raise SkillsError("node and npx were not found.")
        npx_cli = Path(node).resolve().parent / "node_modules" / "npm" / "bin" / "npx-cli.js"
        launcher = [node, str(npx_cli)] if os.name == "nt" and npx_cli.is_file() else [npx]
        command = [*launcher, "--yes", f"skills@{SKILLS_CLI_VERSION}", "update", "--yes"]
        try:
            result = subprocess.run(command, cwd=self.project, capture_output=True, text=True,
                                    timeout=240, check=False, shell=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SkillsError(f"Skills update failed: {type(exc).__name__}") from exc
        if result.returncode != 0:
            raise SkillsError((result.stderr or result.stdout or "Skills update failed")[-800:])
        self._audit("skills.updated", {"cli_version": SKILLS_CLI_VERSION})
        return {"status": "updated", "cli_version": SKILLS_CLI_VERSION}

    async def _maybe_auto_update(self) -> None:
        if not self.auto_update:
            return
        marker = self.root / ".last-auto-update"
        try:
            if marker.is_file() and marker.stat().st_mtime > (time.time() - 86_400):
                return
        except OSError:
            pass
        try:
            await asyncio.to_thread(self.update)
            self.root.mkdir(parents=True, exist_ok=True)
            marker.touch()
        except Exception as exc:
            self._last_error = f"auto_update:{type(exc).__name__}"
            log.warning("automatic skills update skipped: %s", type(exc).__name__)

    async def prepare_for_task(self, request: str) -> tuple[list[dict[str, Any]], str]:
        """Discover one relevant skill, install it if enabled, and return fenced context."""
        await self._maybe_auto_update()
        if not self.auto_discover:
            return self._select_installed(request), self._context(self._select_installed(request))
        discovered = await self.search(request, limit=10)
        installed = {item["name"].casefold() for item in self.list_installed()}
        candidates = [item for item in discovered if item["name"].casefold() not in installed]
        if candidates and self.auto_install:
            # The official search API has already matched the query; install at most one
            # candidate per task. The resulting instructions remain untrusted context.
            candidate = candidates[0]
            try:
                await asyncio.to_thread(self.install, candidate["source"], candidate["name"])
            except Exception as exc:  # optional capability discovery must not block tasks
                self._last_error = f"install:{type(exc).__name__}"
                log.warning("automatic skill install skipped: %s", type(exc).__name__)
        selected = self._select_installed(request)
        return selected, self._context(selected)

    def _select_installed(self, request: str) -> list[dict[str, Any]]:
        terms = _tokens(request)
        ranked: list[tuple[float, dict[str, Any]]] = []
        for item in self.list_installed():
            haystack = f"{item['name']} {item['description']}".casefold()
            matched = [term for term in terms if term in haystack]
            if matched:
                ranked.append((len(matched) / max(1, len(terms)), {**item, "matched_terms": matched[:10]}))
        ranked.sort(key=lambda value: (-value[0], value[1]["name"].casefold()))
        return [item for _, item in ranked[:_MAX_SKILLS]]

    def _context(self, selected: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        remaining = 18_000
        for item in selected:
            path = self.skills_dir / item["directory"] / "SKILL.md"
            try:
                resolved = path.resolve(strict=True)
                resolved.relative_to(self.skills_dir.resolve())
                if path.is_symlink() or not resolved.is_file() or resolved.stat().st_size > 64_000:
                    continue
                content = resolved.read_text(encoding="utf-8", errors="replace")[:_MAX_SKILL_CHARS]
            except (OSError, ValueError):
                continue
            excerpt = content[:remaining]
            if not excerpt.strip():
                continue
            parts.append(
                f"[UNTRUSTED SKILL INSTRUCTIONS: {item['name']}]\n{excerpt}\n"
                "[END UNTRUSTED SKILL INSTRUCTIONS]"
            )
            remaining -= len(excerpt)
            if remaining <= 0:
                break
        return "\n".join(parts)

    def _audit(self, event: str, detail: dict[str, Any]) -> None:
        if self.store is not None:
            self.store.audit(event, actor="skills-manager", status="ok", detail=detail)


__all__ = ["SKILLS_CLI_VERSION", "SkillsError", "SkillsManager"]
