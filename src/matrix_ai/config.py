"""Runtime configuration.

Design rules from the blueprint:

* secrets come from the environment or the OS secret store, never from source;
* local-first defaults, cloud models are opt-in;
* filesystem tools only ever see explicitly approved roots;
* applications and shell commands are allowlisted by ID, never free-form.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

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
    allow_web_fetch: bool = True
    web_fetch_max_bytes: int = 400_000
    cors_origins: tuple[str, ...] = ()

    allowed_apps: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALLOWED_APPS))
    allowed_commands: dict[str, dict] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_ALLOWED_COMMANDS)))
    log_level: str = "INFO"

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
    whatsapp_access_token: str | None = None
    whatsapp_phone_number_id: str | None = None
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
        if any(not re.fullmatch(r"[a-z][a-z0-9_-]*\.[a-z][a-z0-9_-]*", scope) for scope in self.additional_scopes):
            raise ConfigError("MATRIX_EXTRA_SCOPES entries must use domain.action format; wildcard is not allowed")
        if self.youtube_token_path is None:
            self.youtube_token_path = str(self.home / "youtube-token.json")
        self.autonomy_level = max(0, min(4, int(self.autonomy_level)))
        self.model_max_concurrent_requests = max(1, min(8, int(self.model_max_concurrent_requests)))
        self.agent_max_concurrent_workers = max(1, min(16, int(self.agent_max_concurrent_workers)))
        self.agent_worker_timeout_seconds = max(5, min(300, int(self.agent_worker_timeout_seconds)))
        if self.model_provider not in {"none", "ollama", "openai"}:
            raise ConfigError("MATRIX_MODEL_PROVIDER must be one of: none, ollama, openai")

    # -- helpers -----------------------------------------------------------
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
            "autonomy_level": self.autonomy_level,
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "rate_limit_per_minute": self.rate_limit_per_minute,
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
                "youtube": bool(self.youtube_token_path and Path(self.youtube_token_path).is_file()),
                "linkedin": bool(self.linkedin_access_token and self.linkedin_author_urn),
                "instagram": bool(self.instagram_access_token and self.meta_graph_version),
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
            autonomy_level=_env_int("MATRIX_AUTONOMY_LEVEL", 2),
            approval_ttl_seconds=_env_int("MATRIX_APPROVAL_TTL_SECONDS", 300),
            rate_limit_per_minute=_env_int("MATRIX_RATE_LIMIT_PER_MINUTE", 240),
            additional_scopes=tuple(part.strip() for part in (_env("MATRIX_EXTRA_SCOPES", "") or "").split(";") if part.strip()),
            allow_web_fetch=_env_bool("MATRIX_ALLOW_WEB_FETCH", True),
            file_roots=roots,
            allowed_apps=apps,
            log_level=_env("MATRIX_LOG_LEVEL", "INFO") or "INFO",
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
            whatsapp_access_token=_env("MATRIX_WHATSAPP_ACCESS_TOKEN"),
            whatsapp_phone_number_id=_env("MATRIX_WHATSAPP_PHONE_NUMBER_ID"),
            meta_graph_version=_env("MATRIX_META_GRAPH_VERSION"),
        )
