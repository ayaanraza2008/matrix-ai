"""Runtime configuration.

Design rules from the blueprint:

* secrets come from the environment or the OS secret store, never from source;
* local-first defaults, cloud models are opt-in;
* filesystem tools only ever see explicitly approved roots;
* applications and shell commands are allowlisted by ID, never free-form.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ConfigError

TRUE_VALUES = {"1", "true", "yes", "on"}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in TRUE_VALUES


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{name} must be an integer") from exc


def _is_local_host(host: str) -> bool:
    import ipaddress

    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or host.endswith(".internal"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


def default_home() -> Path:
    configured = _env("MATRIX_HOME")
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        base = _env("LOCALAPPDATA") or str(Path.home())
        return Path(base) / "MATRIX"
    return Path.home() / ".matrix"


#: Only these applications may be launched by ID. Anything else is rejected.
DEFAULT_ALLOWED_APPS: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "explorer": "explorer.exe",
    "settings": "ms-settings:",
}

#: Shell execution is limited to these exact argv templates (no model-supplied
#: arguments reach the command line).
DEFAULT_ALLOWED_COMMANDS: dict[str, dict] = {
    "git_status": {
        "argv": ["git", "status", "--short"],
        "description": "Show short git status of the current repository",
        "risk": "medium",
        "timeout": 30,
        "read_only": True,
    },
    "git_log": {
        "argv": ["git", "log", "--oneline", "-n", "10"],
        "description": "Show the last 10 commit subjects",
        "risk": "medium",
        "timeout": 30,
        "read_only": True,
    },
    "python_version": {
        "argv": [sys.executable, "--version"],
        "description": "Report the active Python interpreter version",
        "risk": "low",
        "timeout": 15,
        "read_only": True,
    },
    "pip_version": {
        "argv": [sys.executable, "-m", "pip", "--version"],
        "description": "Report the active pip version",
        "risk": "low",
        "timeout": 30,
        "read_only": True,
    },
    "pytest": {
        "argv": [sys.executable, "-m", "pytest", "-q"],
        "description": "Run the test suite in the current working directory",
        "risk": "medium",
        "timeout": 300,
        "read_only": False,
    },
}


@dataclass
class MatrixConfig:
    """Immutable-ish settings object; one instance per app process."""

    home: Path = field(default_factory=default_home)
    host: str = "127.0.0.1"
    port: int = 8765
    owner_token: str | None = None
    owner_token_generated: bool = False

    db_path: Path | None = None
    workspace_dir: Path | None = None
    file_roots: tuple[Path, ...] = ()
    max_file_bytes: int = 1_000_000
    max_listing_entries: int = 200

    model_provider: str = "none"  # none | ollama | openai
    model_name: str = "qwen2.5:0.5b-instruct-q4_K_M"
    ollama_url: str = "http://127.0.0.1:11434"
    openai_base_url: str = "http://127.0.0.1:8080/v1"
    openai_api_key: str | None = None
    allow_cloud_models: bool = False
    model_ram_budget_mb: int = 2500
    model_request_timeout_s: float = 60.0
    model_idle_unload_s: int = 900
    model_max_concurrent_requests: int = 1

    # Agent actors are many; inference and active worker runs stay resource-bounded.
    agent_max_concurrent_workers: int = 4
    agent_worker_timeout_seconds: int = 90

    autonomy_level: int = 2
    approval_ttl_seconds: int = 300
    approval_wait_seconds: float = 900.0
    max_retries: int = 2
    backoff_base_seconds: float = 0.25
    task_deadline_seconds: float = 900.0

    rate_limit_per_minute: int = 240
    additional_scopes: tuple[str, ...] = ()
    allow_web_fetch: bool = False
    web_fetch_max_bytes: int = 400_000
    cors_origins: tuple[str, ...] = ()

    # Speech output provider: empty/"" keeps the Piper default, "windows-sapi"
    # opts in to the locally installed Windows voice, "none" disables output.
    tts_provider: str = ""

    allowed_apps: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALLOWED_APPS))
    allowed_commands: dict[str, dict] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_ALLOWED_COMMANDS)))
    addon_mcp_servers: dict[str, dict[str, Any]] = field(default_factory=dict)
    addon_auto_install_sources: bool = True
    youtube_automation_enabled: bool = False
    youtube_automation_url: str = "http://127.0.0.1:3456"
    youtube_automation_api_key: str | None = None
    hexstrike_allowed_targets: tuple[str, ...] = ()
    log_level: str = "INFO"

    # Routine recovery notices can be deferred; urgent degradation alerts bypass quiet hours.
    notification_quiet_hours_start: str | None = None
    notification_quiet_hours_end: str | None = None
    notification_timezone: str = "UTC"
    notification_webhook_url: str | None = None
    notification_webhook_secret: str | None = None
    notification_webhook_allowed_hosts: tuple[str, ...] = ()

    # Optional integrations - never required, never logged, never committed.
    github_token: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    home_assistant_url: str | None = None
    home_assistant_token: str | None = None
    google_credentials_path: str | None = None
    twilio_account_sid: str | None = None
    twilio_auth_token: str | None = None
    firebase_credentials_path: str | None = None
    youtube_client_secrets_path: str | None = None
    youtube_token_path: str | None = None
    youtube_max_upload_bytes: int = 2_000_000_000
    linkedin_access_token: str | None = None
    linkedin_author_urn: str | None = None
    linkedin_api_version: str = "202609"
    instagram_access_token: str | None = None
    instagram_user_id: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_allowed_senders: tuple[str, ...] = ()
    meta_graph_version: str | None = None

    def __post_init__(self) -> None:
        self.home = Path(self.home).expanduser().resolve()
        self.db_path = Path(self.db_path) if self.db_path else self.home / "matrix.db"
        self.workspace_dir = Path(self.workspace_dir) if self.workspace_dir else self.home / "workspace"
        for directory in (self.home, self.workspace_dir, Path(self.db_path).parent):
            directory.mkdir(parents=True, exist_ok=True)
        if not self.file_roots:
            self.file_roots = (self.workspace_dir.resolve(),)
        else:
            self.file_roots = tuple(Path(p).expanduser().resolve() for p in self.file_roots)
        if self.owner_token is None:
            env_token = _env("MATRIX_OWNER_TOKEN")
            if env_token:
                self.owner_token = env_token
            else:
                self.owner_token = self._load_or_create_owner_token()
                self.owner_token_generated = True
        self.additional_scopes = tuple(sorted(set(self.additional_scopes)))
        self.whatsapp_allowed_senders = tuple(
            sorted({sender.strip().lstrip("+") for sender in self.whatsapp_allowed_senders if sender.strip()})
        )
        if any(not re.fullmatch(r"[1-9][0-9]{7,14}", sender) for sender in self.whatsapp_allowed_senders):
            raise ConfigError("MATRIX_WHATSAPP_ALLOWED_SENDERS must contain E.164 numbers separated by semicolons")
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*", scope) for scope in self.additional_scopes):
            raise ConfigError("MATRIX_EXTRA_SCOPES entries must use domain.action format; wildcard is not allowed")
        if self.youtube_token_path is None:
            self.youtube_token_path = str(self.home / "youtube-token.json")
        self.autonomy_level = max(0, min(4, int(self.autonomy_level)))
        self.model_max_concurrent_requests = max(1, min(8, int(self.model_max_concurrent_requests)))
        self.agent_max_concurrent_workers = max(1, min(16, int(self.agent_max_concurrent_workers)))
        self.agent_worker_timeout_seconds = max(5, min(300, int(self.agent_worker_timeout_seconds)))
        self.addon_mcp_servers = self._validate_addon_servers(self.addon_mcp_servers)
        self.hexstrike_allowed_targets = self._validate_hexstrike_targets(self.hexstrike_allowed_targets)
        from urllib.parse import urlparse

        parsed_youtube_url = urlparse(self.youtube_automation_url)
        if self.youtube_automation_enabled and (
            parsed_youtube_url.scheme not in {"http", "https"}
            or not parsed_youtube_url.hostname
            or not _is_local_host(parsed_youtube_url.hostname)
        ):
            raise ConfigError("MATRIX_YOUTUBE_AUTOMATION_URL must be loopback or private-network only")
        if self.model_provider not in {"none", "ollama", "openai"}:
            raise ConfigError("MATRIX_MODEL_PROVIDER must be one of: none, ollama, openai")
        if bool(self.notification_quiet_hours_start) != bool(self.notification_quiet_hours_end):
            raise ConfigError("notification quiet hours require both start and end times")
        for label, value in (
            ("MATRIX_NOTIFICATION_QUIET_HOURS_START", self.notification_quiet_hours_start),
            ("MATRIX_NOTIFICATION_QUIET_HOURS_END", self.notification_quiet_hours_end),
        ):
            if value is not None:
                try:
                    parsed = datetime_time.fromisoformat(value)
                except ValueError as exc:
                    raise ConfigError(f"{label} must use HH:MM format") from exc
                if parsed.second or parsed.microsecond or len(value) != 5:
                    raise ConfigError(f"{label} must use HH:MM format")
        try:
            ZoneInfo(self.notification_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError("MATRIX_NOTIFICATION_TIMEZONE must be a valid IANA timezone") from exc
        webhook_values = (self.notification_webhook_url, self.notification_webhook_secret)
        if any(webhook_values) and not all(webhook_values):
            raise ConfigError("notification webhook URL and signing secret must be configured together")
        if self.notification_webhook_url:
            try:
                parsed = urlsplit(self.notification_webhook_url)
                port = parsed.port
            except ValueError as exc:
                raise ConfigError("MATRIX_NOTIFICATION_WEBHOOK_URL is invalid") from exc
            host = (parsed.hostname or "").lower().rstrip(".")
            allowed_hosts = {item.lower().rstrip(".") for item in self.notification_webhook_allowed_hosts}
            if (
                parsed.scheme != "https" or not host or "." not in host
                or host == "localhost" or host.endswith((".localhost", ".local", ".localdomain"))
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or port not in (None, 443) or host not in allowed_hosts
            ):
                raise ConfigError(
                    "notification webhook must be HTTPS on an explicitly allowed host and use the default port"
                )
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                address = None
            if address is not None and (
                address.is_private or address.is_loopback or address.is_link_local
                or address.is_multicast or address.is_reserved or address.is_unspecified
            ):
                raise ConfigError("notification webhook host must not be a private or special-use IP address")

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _validate_hexstrike_targets(raw: Any) -> tuple[str, ...]:
        import ipaddress
        import re

        values = raw.split(",") if isinstance(raw, str) else raw
        if not isinstance(values, (list, tuple)) or len(values) > 100:
            raise ConfigError("MATRIX_HEXSTRIKE_ALLOWED_TARGETS must be a bounded comma-separated list")
        result: set[str] = set()
        for item in values:
            if not isinstance(item, str):
                raise ConfigError("HexStrike targets must be IP addresses or exact hostnames")
            host = item.strip().casefold().rstrip(".")
            if not host or len(host) > 253 or any(char in host for char in "/\\*@ "):
                raise ConfigError("HexStrike targets must be exact IP addresses or hostnames, not URLs or wildcards")
            try:
                ipaddress.ip_address(host)
            except ValueError:
                if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*", host):
                    raise ConfigError(f"Invalid HexStrike target hostname: {host}") from None
            result.add(host)
        return tuple(sorted(result))

    @staticmethod
    def _validate_addon_servers(raw: Any) -> dict[str, dict[str, Any]]:
        from .integrations.addon_catalog import ADDON_BY_ID

        if raw is None:
            return {}
        if not isinstance(raw, dict) or len(raw) > 18:
            raise ConfigError("MATRIX_ADDON_MCP_SERVERS must be a JSON object with at most 18 entries")
        result: dict[str, dict[str, Any]] = {}
        for addon_id, spec in raw.items():
            addon = ADDON_BY_ID.get(addon_id) if isinstance(addon_id, str) else None
            if addon is None or addon["protocol"] not in {"mcp_stdio", "mcp_candidate", "desktop_mcp", "security_mcp_bridge"}:
                raise ConfigError(f"MATRIX_ADDON_MCP_SERVERS has an unsupported add-on id: {addon_id}")
            if not isinstance(spec, dict):
                raise ConfigError(f"MCP server configuration for {addon_id} must be an object")
            transport = spec.get("transport", "stdio")
            args = spec.get("args", [])
            env = spec.get("env", {})
            headers = spec.get("headers", {})
            command = spec.get("command", "")
            url = spec.get("url", "")
            if transport == "streamable_http":
                from urllib.parse import urlparse

                parsed = urlparse(url if isinstance(url, str) else "")
                if addon_id != "openwhispr" or parsed.scheme != "https" or parsed.hostname != "mcp.openwhispr.com" or parsed.path != "/mcp":
                    raise ConfigError("Only the verified OpenWhispr remote MCP URL is supported for streamable HTTP.")
                if not isinstance(headers, dict) or len(headers) > 16 or any(
                    not isinstance(key, str) or not isinstance(value, str)
                    or len(key) > 100 or len(value) > 8000
                    for key, value in headers.items()
                ):
                    raise ConfigError("MCP HTTP headers must be a bounded string map")
                if not any(key.casefold() == "authorization" for key in headers):
                    raise ConfigError("OpenWhispr remote MCP needs an owner-supplied Authorization bearer key.")
            elif transport == "stdio":
                if not isinstance(command, str) or not command.strip() or len(command) > 300:
                    raise ConfigError(f"MCP server {addon_id} requires a bounded command string")
                if not isinstance(args, list) or len(args) > 64 or any(not isinstance(arg, str) or len(arg) > 2000 for arg in args):
                    raise ConfigError(f"MCP server arguments for {addon_id} must be a list of strings")
                if not isinstance(env, dict) or len(env) > 64 or any(
                    not isinstance(key, str) or not key.isidentifier() or len(key) > 80
                    or not isinstance(value, str) or len(value) > 8000
                    for key, value in env.items()
                ):
                    raise ConfigError(f"MCP server environment for {addon_id} must be a bounded string map")
            else:
                raise ConfigError(f"Unsupported MCP transport for {addon_id}: {transport}")
            read_only_tools = spec.get("read_only_tools", [])
            if not isinstance(read_only_tools, list) or len(read_only_tools) > 128 or any(
                not isinstance(name, str) or not name or len(name) > 160 for name in read_only_tools
            ):
                raise ConfigError(f"read_only_tools for {addon_id} must be a list of bounded tool names")
            result[addon_id] = {
                "transport": transport,
                "command": command if isinstance(command, str) else "",
                "args": list(args) if isinstance(args, list) else [],
                "env": dict(env) if isinstance(env, dict) else {},
                "url": url if isinstance(url, str) else "",
                "headers": dict(headers) if isinstance(headers, dict) else {},
                "read_only_tools": sorted(set(read_only_tools)),
            }
        return result

    def _load_or_create_owner_token(self) -> str:
        token_file = self.home / "owner_token.txt"
        if token_file.exists():
            value = token_file.read_text(encoding="utf-8").strip()
            if value:
                return value
        token = secrets.token_urlsafe(32)
        token_file.write_text(token, encoding="utf-8")
        try:  # best-effort on POSIX; Windows ACLs are managed by the OS profile
            token_file.chmod(0o600)
        except OSError:  # pragma: no cover - platform dependent
            pass
        return token

    def is_loopback(self) -> bool:
        return self.host in {"127.0.0.1", "localhost", "::1"}

    def in_allowed_root(self, path: Path) -> bool:
        candidate = Path(path).expanduser().resolve()
        for root in self.file_roots:
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def public_dict(self) -> dict:
        """Safe-to-render configuration (never includes tokens/keys)."""
        try:
            from .integrations.youtube_auth import has_youtube_credentials

            youtube_connected = has_youtube_credentials(self)
        except Exception:  # noqa: BLE001 - public config rendering must fail closed
            youtube_connected = False
        return {
            "home": str(self.home),
            "host": self.host,
            "port": self.port,
            "loopback_only": self.is_loopback(),
            "db_path": str(self.db_path),
            "workspace_dir": str(self.workspace_dir),
            "file_roots": [str(p) for p in self.file_roots],
            "model_provider": self.model_provider,
            "model_name": self.model_name,
            "allow_cloud_models": self.allow_cloud_models,
            "model_ram_budget_mb": self.model_ram_budget_mb,
            "model_max_concurrent_requests": self.model_max_concurrent_requests,
            "agent_max_concurrent_workers": self.agent_max_concurrent_workers,
            "agent_worker_timeout_seconds": self.agent_worker_timeout_seconds,
            "configured_addon_mcp_servers": sorted(self.addon_mcp_servers),
            "addon_auto_install_sources": self.addon_auto_install_sources,
            "youtube_automation_enabled": self.youtube_automation_enabled,
            "youtube_automation_api_key_present": bool(self.youtube_automation_api_key),
            "hexstrike_allowed_targets": list(self.hexstrike_allowed_targets),
            "autonomy_level": self.autonomy_level,
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
            "notification_routing": {
                "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
                "quiet_hours_start": self.notification_quiet_hours_start,
                "quiet_hours_end": self.notification_quiet_hours_end,
                "timezone": self.notification_timezone,
                "urgent_degradation_bypasses_quiet_hours": True,
                "webhook_configured": bool(self.notification_webhook_url and self.notification_webhook_secret),
            },
            "allow_web_fetch": self.allow_web_fetch,
            "allowed_app_ids": sorted(self.allowed_apps),
            "allowed_command_ids": sorted(self.allowed_commands),
            "credentials_present": True,
            "integrations_configured": {
                "github": bool(self.github_token),
                "telegram": bool(self.telegram_bot_token and self.telegram_chat_id),
                "google_workspace": bool(self.google_credentials_path),
                "home_assistant": bool(self.home_assistant_url and self.home_assistant_token),
                "telephony": bool(self.twilio_account_sid and self.twilio_auth_token),
                "firebase_push": bool(self.firebase_credentials_path),
                "youtube": youtube_connected,
                "linkedin": bool(self.linkedin_access_token and self.linkedin_author_urn),
                "instagram": bool(self.instagram_access_token and self.instagram_user_id and self.meta_graph_version),
                "whatsapp": bool(self.whatsapp_access_token and self.whatsapp_phone_number_id and self.meta_graph_version),
            },
        }

    @classmethod
    def from_env(cls) -> MatrixConfig:
        apps = dict(DEFAULT_ALLOWED_APPS)
        raw_apps = _env("MATRIX_ALLOWED_APPS")
        if raw_apps:
            try:
                apps.update({str(k): str(v) for k, v in json.loads(raw_apps).items()})
            except (ValueError, AttributeError) as exc:
                raise ConfigError("MATRIX_ALLOWED_APPS must be a JSON object") from exc

        raw_addon_servers = _env("MATRIX_ADDON_MCP_SERVERS")
        try:
            addon_servers = json.loads(raw_addon_servers) if raw_addon_servers else {}
        except ValueError as exc:
            raise ConfigError("MATRIX_ADDON_MCP_SERVERS must be valid JSON") from exc

        roots: tuple[Path, ...] = ()
        raw_roots = _env("MATRIX_FILE_ROOTS")
        if raw_roots:
            roots = tuple(Path(part.strip()) for part in raw_roots.split(";") if part.strip())

        return cls(
            home=default_home(),
            host=_env("MATRIX_HOST", "127.0.0.1") or "127.0.0.1",
            port=_env_int("MATRIX_PORT", 8765),
            owner_token=_env("MATRIX_OWNER_TOKEN"),
            model_provider=(_env("MATRIX_MODEL_PROVIDER", "none") or "none").lower(),
            model_name=_env("MATRIX_MODEL_NAME", "qwen2.5:0.5b-instruct-q4_K_M") or "qwen2.5:0.5b-instruct-q4_K_M",
            ollama_url=_env("MATRIX_OLLAMA_URL", "http://127.0.0.1:11434") or "http://127.0.0.1:11434",
            openai_base_url=_env("MATRIX_OPENAI_BASE_URL", "http://127.0.0.1:8080/v1")
            or "http://127.0.0.1:8080/v1",
            openai_api_key=_env("MATRIX_OPENAI_API_KEY"),
            allow_cloud_models=_env_bool("MATRIX_ALLOW_CLOUD_MODELS", False),
            model_ram_budget_mb=_env_int("MATRIX_MODEL_RAM_BUDGET_MB", 2500),
            model_max_concurrent_requests=_env_int("MATRIX_MODEL_MAX_CONCURRENT_REQUESTS", 1),
            agent_max_concurrent_workers=_env_int("MATRIX_AGENT_MAX_CONCURRENT_WORKERS", 4),
            agent_worker_timeout_seconds=_env_int("MATRIX_AGENT_WORKER_TIMEOUT_SECONDS", 90),
            youtube_automation_enabled=_env_bool("MATRIX_YOUTUBE_AUTOMATION_ENABLED", False),
            youtube_automation_url=_env("MATRIX_YOUTUBE_AUTOMATION_URL", "http://127.0.0.1:3456") or "http://127.0.0.1:3456",
            youtube_automation_api_key=_env("MATRIX_YOUTUBE_AUTOMATION_API_KEY"),
            hexstrike_allowed_targets=tuple(
                part.strip() for part in (_env("MATRIX_HEXSTRIKE_ALLOWED_TARGETS", "") or "").split(",") if part.strip()
            ),
            autonomy_level=_env_int("MATRIX_AUTONOMY_LEVEL", 2),
            approval_ttl_seconds=_env_int("MATRIX_APPROVAL_TTL_SECONDS", 300),
            rate_limit_per_minute=_env_int("MATRIX_RATE_LIMIT_PER_MINUTE", 240),
            additional_scopes=tuple(part.strip() for part in (_env("MATRIX_EXTRA_SCOPES", "") or "").split(";") if part.strip()),
            allow_web_fetch=_env_bool("MATRIX_ALLOW_WEB_FETCH", False),
            tts_provider=(_env("MATRIX_TTS_PROVIDER", "") or "").strip().lower(),
            file_roots=roots,
            allowed_apps=apps,
            addon_mcp_servers=addon_servers,
            addon_auto_install_sources=_env_bool("MATRIX_ADDON_AUTO_INSTALL_SOURCES", True),
            log_level=_env("MATRIX_LOG_LEVEL", "INFO") or "INFO",
            notification_quiet_hours_start=_env("MATRIX_NOTIFICATION_QUIET_HOURS_START"),
            notification_quiet_hours_end=_env("MATRIX_NOTIFICATION_QUIET_HOURS_END"),
            notification_timezone=_env("MATRIX_NOTIFICATION_TIMEZONE", "UTC") or "UTC",
            notification_webhook_url=_env("MATRIX_NOTIFICATION_WEBHOOK_URL"),
            notification_webhook_secret=_env("MATRIX_NOTIFICATION_WEBHOOK_SECRET"),
            notification_webhook_allowed_hosts=tuple(
                item.strip() for item in (_env("MATRIX_NOTIFICATION_WEBHOOK_ALLOWED_HOSTS", "") or "").split(",") if item.strip()
            ),
            github_token=_env("MATRIX_GITHUB_TOKEN"),
            telegram_bot_token=_env("MATRIX_TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=_env("MATRIX_TELEGRAM_CHAT_ID"),
            home_assistant_url=_env("MATRIX_HOME_ASSISTANT_URL"),
            home_assistant_token=_env("MATRIX_HOME_ASSISTANT_TOKEN"),
            google_credentials_path=_env("MATRIX_GOOGLE_CREDENTIALS"),
            twilio_account_sid=_env("MATRIX_TWILIO_ACCOUNT_SID"),
            twilio_auth_token=_env("MATRIX_TWILIO_AUTH_TOKEN"),
            firebase_credentials_path=_env("MATRIX_FIREBASE_CREDENTIALS"),
            youtube_client_secrets_path=_env("MATRIX_YOUTUBE_CLIENT_SECRETS"),
            youtube_token_path=_env("MATRIX_YOUTUBE_TOKEN_FILE"),
            youtube_max_upload_bytes=_env_int("MATRIX_YOUTUBE_MAX_UPLOAD_BYTES", 2_000_000_000),
            linkedin_access_token=_env("MATRIX_LINKEDIN_ACCESS_TOKEN"),
            linkedin_author_urn=_env("MATRIX_LINKEDIN_AUTHOR_URN"),
            linkedin_api_version=_env("MATRIX_LINKEDIN_API_VERSION", "202609") or "202609",
            instagram_access_token=_env("MATRIX_INSTAGRAM_ACCESS_TOKEN"),
            instagram_user_id=_env("MATRIX_INSTAGRAM_USER_ID"),
            whatsapp_access_token=_env("MATRIX_WHATSAPP_ACCESS_TOKEN"),
            whatsapp_phone_number_id=_env("MATRIX_WHATSAPP_PHONE_NUMBER_ID"),
            whatsapp_app_secret=_env("MATRIX_WHATSAPP_APP_SECRET"),
            whatsapp_verify_token=_env("MATRIX_WHATSAPP_WEBHOOK_VERIFY_TOKEN"),
            whatsapp_allowed_senders=tuple(
                part.strip().lstrip("+")
                for part in (_env("MATRIX_WHATSAPP_ALLOWED_SENDERS", "") or "").split(";")
                if part.strip()
            ),
            meta_graph_version=_env("MATRIX_META_GRAPH_VERSION"),
        )
