"""Local OAuth helpers for the official YouTube Data API.

Refresh tokens are stored in the operating-system credential manager via
keyring. Insecure/fallback keyring backends are rejected; no plaintext token
file is created. Provider dependencies are imported lazily.
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
_KEYRING_SERVICE = "MATRIX AI / YouTube OAuth"


def _credential_store():
    try:
        import keyring
        backend = keyring.get_keyring()
        priority = getattr(backend, "priority", 0)
        if not isinstance(priority, (int, float)) or priority <= 0:
            raise RuntimeError("no secure OS credential backend is available")
        return keyring
    except Exception as exc:  # noqa: BLE001 - keyring backends vary by operating system
        raise ToolExecutionError(
            "A secure OS credential manager is unavailable. Install the YouTube extra and configure "
            "Windows Credential Manager, macOS Keychain, or a SecretService keyring."
        ) from exc


def _credential_user(config: MatrixConfig) -> str:
    return str(Path(config.home).expanduser().resolve())


def has_youtube_credentials(config: MatrixConfig) -> bool:
    try:
        keyring = _credential_store()
        return bool(keyring.get_password(_KEYRING_SERVICE, _credential_user(config)))
    except Exception:
        return False


def _persist_credentials(config: MatrixConfig, credentials: Any) -> None:
    keyring = _credential_store()
    username = _credential_user(config)
    payload = credentials.to_json()
    try:
        keyring.set_password(_KEYRING_SERVICE, username, payload)
        if keyring.get_password(_KEYRING_SERVICE, username) != payload:
            raise RuntimeError("credential-store read-back did not match")
    except Exception as exc:  # noqa: BLE001 - never expose credential-store internals
        raise ToolExecutionError("Could not securely save YouTube credentials in the OS credential manager.") from exc


def load_youtube_credentials(config: MatrixConfig, required_scope: str):
    keyring = _credential_store()
    try:
        payload = keyring.get_password(_KEYRING_SERVICE, _credential_user(config))
    except Exception as exc:  # noqa: BLE001 - keyring may be locked or unavailable
        raise ToolExecutionError("Could not access the OS credential manager for YouTube.") from exc
    if not payload:
        raise ToolExecutionError(
            "YouTube is not connected. Configure MATRIX_YOUTUBE_CLIENT_SECRETS and run 'matrix youtube-auth'."
        )
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise ToolExecutionError("Install MATRIX's optional YouTube dependencies: pip install -e '.[youtube]'.") from exc

    try:
        credentials = Credentials.from_authorized_user_info(__import__("json").loads(payload))
    except (TypeError, ValueError) as exc:
        raise ToolExecutionError("The saved YouTube credentials in the OS credential manager are invalid.") from exc

    if credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
            _persist_credentials(config, credentials)
        except Exception as exc:  # noqa: BLE001 - never expose OAuth response bodies or tokens
            raise ToolExecutionError("YouTube OAuth refresh failed. Reconnect with 'matrix youtube-auth'.") from exc
    if not credentials.valid:
        raise ToolExecutionError("YouTube OAuth credentials are expired or invalid. Reconnect with 'matrix youtube-auth'.")

    granted_scopes = set(credentials.granted_scopes or credentials.scopes or [])
    if required_scope not in granted_scopes:
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


def connect_youtube(config: MatrixConfig) -> str:
    """Run the explicit browser-based owner consent flow and persist in the OS credential manager."""
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
    _persist_credentials(config, credentials)
    return "OS credential manager"
