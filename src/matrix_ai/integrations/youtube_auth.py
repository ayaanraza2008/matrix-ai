"""Local OAuth helpers for the official YouTube Data API.

OAuth tokens are stored under MATRIX_HOME by default and are never returned by
API endpoints or included in logs. Provider dependencies are imported lazily.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import MatrixConfig
from ..errors import ToolExecutionError

YOUTUBE_READ_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
YOUTUBE_WRITE_SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_OAUTH_SCOPES = [YOUTUBE_READ_SCOPE, YOUTUBE_WRITE_SCOPE, YOUTUBE_UPLOAD_SCOPE]


def token_path(config: MatrixConfig) -> Path:
    return Path(config.youtube_token_path or (config.home / "youtube-token.json")).expanduser()


def _persist_credentials(path: Path, credentials: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    try:
        temporary.chmod(0o600)
    except OSError:  # pragma: no cover - Windows ACLs are managed by the user profile
        pass
    temporary.replace(path)


def load_youtube_credentials(config: MatrixConfig, required_scope: str):
    path = token_path(config)
    if not path.is_file():
        raise ToolExecutionError(
            "YouTube is not connected. Configure MATRIX_YOUTUBE_CLIENT_SECRETS and run 'matrix youtube-auth'."
        )
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise ToolExecutionError("Install MATRIX's optional YouTube dependencies: pip install -e '.[youtube]'.") from exc

    try:
        credentials = Credentials.from_authorized_user_file(str(path), scopes=YOUTUBE_OAUTH_SCOPES)
    except (OSError, ValueError) as exc:
        raise ToolExecutionError("The local YouTube OAuth token file is missing or invalid.") from exc

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _persist_credentials(path, credentials)
        except Exception as exc:  # noqa: BLE001 - never expose OAuth response bodies or tokens
            raise ToolExecutionError("YouTube OAuth refresh failed. Reconnect with 'matrix youtube-auth'.") from exc
    if not credentials.valid:
        raise ToolExecutionError("YouTube OAuth credentials are expired or invalid. Reconnect with 'matrix youtube-auth'.")

    if not credentials.has_scopes([required_scope]):
        raise ToolExecutionError(
            "The saved YouTube token lacks the required permission. Re-run 'matrix youtube-auth' to grant the updated scopes."
        )
    return credentials


def build_youtube_service(config: MatrixConfig, required_scope: str):
    credentials = load_youtube_credentials(config, required_scope)
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise ToolExecutionError("Install MATRIX's optional YouTube dependencies: pip install -e '.[youtube]'.") from exc
    try:
        return build("youtube", "v3", credentials=credentials, cache_discovery=False)
    except Exception as exc:  # noqa: BLE001 - provider internals may contain sensitive request metadata
        raise ToolExecutionError("Could not initialize the YouTube Data API client.") from exc


def connect_youtube(config: MatrixConfig) -> Path:
    """Run the explicit browser-based owner consent flow and persist the refresh token."""
    if not config.youtube_client_secrets_path:
        raise ToolExecutionError("Set MATRIX_YOUTUBE_CLIENT_SECRETS to your Google Desktop OAuth client JSON first.")
    client_secrets = Path(config.youtube_client_secrets_path).expanduser()
    if not client_secrets.is_file():
        raise ToolExecutionError("The configured Google OAuth client JSON file does not exist.")
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise ToolExecutionError("Install MATRIX's optional YouTube dependencies: pip install -e '.[youtube]'.") from exc

    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), scopes=YOUTUBE_OAUTH_SCOPES)
        credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    except Exception as exc:  # noqa: BLE001 - do not leak OAuth state, codes or tokens
        raise ToolExecutionError("YouTube OAuth consent did not complete.") from exc
    path = token_path(config)
    _persist_credentials(path, credentials)
    return path
