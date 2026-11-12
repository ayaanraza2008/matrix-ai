"""Small, bounded clients for official Meta Graph and LinkedIn REST APIs.

Access tokens are sent only in Authorization headers. Response bodies and
provider error details are not logged because they may contain personal data.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..config import MatrixConfig
from ..errors import ToolExecutionError

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


def _request(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        with httpx.Client(timeout=_TIMEOUT, follow_redirects=False) as client:
            response = client.request(method, url, headers=headers, params=params, json=body)
            response.raise_for_status()
            payload = response.json() if response.content else {}
            if not isinstance(payload, dict):
                raise ToolExecutionError("Social provider returned an unexpected response format.")
            return payload, dict(response.headers)
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            message = "Social account authorization expired or was revoked."
        elif status == 403:
            message = "Social provider denied this action; check account permissions and API access."
        elif status == 429:
            message = "Social provider rate limit or quota was reached."
        elif status == 404:
            message = "Social provider resource was not found or is not accessible."
        else:
            message = f"Social provider request failed with HTTP {status}."
        raise ToolExecutionError(message) from exc
    except (httpx.RequestError, ValueError) as exc:
        raise ToolExecutionError("Social provider could not be reached or returned invalid JSON.") from exc


def meta_request(
    config: MatrixConfig,
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    token = config.instagram_access_token
    if not token:
        raise ToolExecutionError("Instagram is not connected; configure MATRIX_INSTAGRAM_ACCESS_TOKEN.")
    version = config.meta_graph_version.strip()
    if not version.startswith("v") or not version[1:].replace(".", "").isdigit():
        raise ToolExecutionError("MATRIX_META_GRAPH_VERSION must look like v22.0.")
    path = endpoint.lstrip("/")
    return _request(
        method,
        f"https://graph.facebook.com/{version}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        params=params,
        body=body,
    )


def linkedin_request(
    config: MatrixConfig,
    method: str,
    endpoint: str,
    *,
    params: dict[str, Any] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    token = config.linkedin_access_token
    if not token:
        raise ToolExecutionError("LinkedIn is not connected; configure MATRIX_LINKEDIN_ACCESS_TOKEN.")
    version = config.linkedin_api_version.strip()
    if len(version) != 6 or not version.isdigit():
        raise ToolExecutionError("MATRIX_LINKEDIN_API_VERSION must be a six-digit YYYYMM value.")
    path = endpoint.lstrip("/")
    return _request(
        method,
        f"https://api.linkedin.com/{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "LinkedIn-Version": version,
            "X-Restli-Protocol-Version": "2.0.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        params=params,
        body=body,
    )


def encode_urn(value: str) -> str:
    if not value.startswith(("urn:li:activity:", "urn:li:share:", "urn:li:ugcPost:", "urn:li:post:")):
        raise ToolExecutionError("LinkedIn post URN has an unsupported format.")
    return quote(value, safe="")
