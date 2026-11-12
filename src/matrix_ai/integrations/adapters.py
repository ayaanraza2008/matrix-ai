"""Optional integration adapters.

Every network check is explicit, time-bounded and only reads public/account
metadata. Nothing here runs during startup, and no adapter sends user content.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from ..config import MatrixConfig
from .base import (
    STATUS_CONFIGURED,
    STATUS_DEGRADED,
    STATUS_DENIED,
    STATUS_MISSING,
    STATUS_OK,
    STATUS_RATE_LIMITED,
    STATUS_UNAVAILABLE,
    Integration,
    IntegrationResult,
)

TIMEOUT = httpx.Timeout(6.0, connect=4.0)


class GitHubIntegration(Integration):
    name = "github"
    description = "Inspect repositories, issues and pull requests with a least-privilege token."
    category = "developer"
    required_settings = ("MATRIX_GITHUB_TOKEN",)

    @property
    def configured(self) -> bool:
        return bool(self.config.github_token)

    async def check(self) -> IntegrationResult:
        headers = {
            "Authorization": f"Bearer {self.config.github_token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "MATRIX/0.1",
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get("https://api.github.com/user", headers=headers)
        if response.status_code == 200:
            remaining = response.headers.get("x-ratelimit-remaining", "?")
            data = response.json()
            return IntegrationResult(STATUS_OK, f"Authenticated as {data.get('login', 'unknown')}; rate limit left: {remaining}.")
        if response.status_code == 401:
            return IntegrationResult(STATUS_DENIED, "GitHub rejected the token (401).")
        if response.status_code == 403:
            return IntegrationResult(STATUS_RATE_LIMITED, "GitHub rate limit or permission problem (403).")
        return IntegrationResult(STATUS_DEGRADED, f"GitHub returned HTTP {response.status_code}.")


class TelegramIntegration(Integration):
    name = "telegram"
    description = "Send MATRIX notifications to a specific Telegram chat via a bot."
    category = "notifications"
    required_settings = ("MATRIX_TELEGRAM_BOT_TOKEN", "MATRIX_TELEGRAM_CHAT_ID")

    @property
    def configured(self) -> bool:
        return bool(self.config.telegram_bot_token and self.config.telegram_chat_id)

    async def check(self) -> IntegrationResult:
        url = f"https://api.telegram.org/bot{self.config.telegram_bot_token}/getMe"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url)
        if response.status_code == 200 and response.json().get("ok"):
            return IntegrationResult(STATUS_OK, "Bot token accepted. Messages are sent only to the configured chat ID.")
        if response.status_code in {401, 404}:
            return IntegrationResult(STATUS_DENIED, "Telegram rejected the bot token.")
        return IntegrationResult(STATUS_DEGRADED, f"Telegram returned HTTP {response.status_code}.")


class GoogleWorkspaceIntegration(Integration):
    name = "google_workspace"
    description = "Gmail and Calendar access through a user-owned OAuth client."
    category = "productivity"
    required_settings = ("MATRIX_GOOGLE_CREDENTIALS",)

    @property
    def configured(self) -> bool:
        return bool(self.config.google_credentials_path)

    async def check(self) -> IntegrationResult:
        path = Path(str(self.config.google_credentials_path)).expanduser()
        if not path.is_file():
            return IntegrationResult(STATUS_MISSING, "Credentials file was not found at the configured path.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return IntegrationResult(STATUS_DEGRADED, "Credentials file is not valid JSON.")
        if not isinstance(payload, dict) or not ({"installed", "web"} & set(payload)):
            return IntegrationResult(STATUS_DEGRADED, "Credentials file is not an OAuth client document.")
        return IntegrationResult(
            STATUS_CONFIGURED,
            "OAuth client present. Gmail/Calendar scopes and the consent flow are a manual setup step.",
        )


class HomeAssistantIntegration(Integration):
    name = "home_assistant"
    description = "Read and control Home Assistant entities on the local network."
    category = "smart_home"
    required_settings = ("MATRIX_HOME_ASSISTANT_URL", "MATRIX_HOME_ASSISTANT_TOKEN")

    @property
    def configured(self) -> bool:
        return bool(self.config.home_assistant_url and self.config.home_assistant_token)

    async def check(self) -> IntegrationResult:
        base = str(self.config.home_assistant_url).rstrip("/")
        headers = {
            "Authorization": f"Bearer {self.config.home_assistant_token}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(f"{base}/api/", headers=headers)
        if response.status_code == 200:
            return IntegrationResult(STATUS_OK, "Home Assistant API reachable and token accepted.")
        if response.status_code == 401:
            return IntegrationResult(STATUS_DENIED, "Home Assistant rejected the token (401).")
        return IntegrationResult(STATUS_DEGRADED, f"Home Assistant returned HTTP {response.status_code}.")


class WeatherIntegration(Integration):
    name = "weather"
    description = "Free Open-Meteo forecast lookup (no API key required)."
    category = "information"
    required_settings = ()

    @property
    def configured(self) -> bool:
        return True

    async def check(self) -> IntegrationResult:
        params = {"latitude": 0.0, "longitude": 0.0, "current": "temperature_2m"}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        if response.status_code == 200:
            return IntegrationResult(STATUS_OK, "Open-Meteo responded. No key is required.")
        return IntegrationResult(STATUS_DEGRADED, f"Open-Meteo returned HTTP {response.status_code}.")


class TelephonyIntegration(Integration):
    name = "telephony"
    description = "Optional PSTN calls/SMS via Twilio. Provider minutes are billed - not free."
    category = "paid_optional"
    required_settings = ("MATRIX_TWILIO_ACCOUNT_SID", "MATRIX_TWILIO_AUTH_TOKEN")

    @property
    def configured(self) -> bool:
        return bool(self.config.twilio_account_sid and self.config.twilio_auth_token)

    async def check(self) -> IntegrationResult:
        sid = str(self.config.twilio_account_sid)
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json"
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(url, auth=(sid, str(self.config.twilio_auth_token)))
        if response.status_code == 200:
            return IntegrationResult(
                STATUS_OK, "Twilio account reachable. Calls and SMS incur provider charges."
            )
        if response.status_code == 401:
            return IntegrationResult(STATUS_DENIED, "Twilio rejected the credentials (401).")
        return IntegrationResult(STATUS_DEGRADED, f"Twilio returned HTTP {response.status_code}.")


class FirebasePushIntegration(Integration):
    name = "firebase_push"
    description = "Android push notifications through Firebase Cloud Messaging."
    category = "notifications"
    required_settings = ("MATRIX_FIREBASE_CREDENTIALS",)

    @property
    def configured(self) -> bool:
        return bool(self.config.firebase_credentials_path)

    async def check(self) -> IntegrationResult:
        path = Path(str(self.config.firebase_credentials_path)).expanduser()
        if not path.is_file():
            return IntegrationResult(STATUS_MISSING, "Service-account file was not found at the configured path.")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return IntegrationResult(STATUS_DEGRADED, "Service-account file is not valid JSON.")
        if not isinstance(payload, dict) or payload.get("type") != "service_account":
            return IntegrationResult(STATUS_DEGRADED, "File is not a Firebase service-account document.")
        return IntegrationResult(
            STATUS_CONFIGURED, "Service account present. Android device registration is a setup step."
        )


def build_integrations(config: MatrixConfig) -> list[Integration]:
    return [
        GitHubIntegration(config),
        TelegramIntegration(config),
        GoogleWorkspaceIntegration(config),
        HomeAssistantIntegration(config),
        WeatherIntegration(config),
        FirebasePushIntegration(config),
        TelephonyIntegration(config),
    ]


__all__ = [
    "FirebasePushIntegration",
    "GitHubIntegration",
    "GoogleWorkspaceIntegration",
    "HomeAssistantIntegration",
    "IntegrationResult",
    "STATUS_UNAVAILABLE",
    "TelegramIntegration",
    "TelephonyIntegration",
    "WeatherIntegration",
    "build_integrations",
]
