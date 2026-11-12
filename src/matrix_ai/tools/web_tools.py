"""Public web fetch for research, with SSRF protection.

Only http(s), only public addresses (private/loopback/link-local/reserved are
refused), redirects are *not* followed automatically, response size is bounded,
and the body is returned as untrusted data with injection markers surfaced.
"""

from __future__ import annotations

import html as html_module
import ipaddress
import re
import socket
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import httpx
from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import PermissionDenied, ToolExecutionError, ToolUnavailable, ValidationFailure
from ..security.untrusted import detect_injection
from .base import StrictModel, Tool, ToolContext

ALLOWED_PORTS = {80, 443, 8080, 8443}
ALLOWED_CONTENT_TYPES = (
    "text/html",
    "text/plain",
    "text/xml",
    "application/xhtml+xml",
    "application/json",
    "application/xml",
)
_SCRIPT_BLOCKS = re.compile(r"(?is)<(script|style|noscript|svg|iframe)[^>]*>.*?</\1>")
_TAGS = re.compile(r"(?s)<[^>]+>")
_TITLE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_BLANKS = re.compile(r"\n{3,}")

Resolver = Callable[..., list]


def resolve_and_validate(url: str, resolver: Resolver | None = None) -> list[str]:
    """Validate a URL can only reach the public internet. Returns resolved IPs."""
    resolver = resolver or socket.getaddrinfo
    if not isinstance(url, str) or len(url) > 2000:
        raise ValidationFailure("url must be a string shorter than 2000 characters")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise PermissionDenied("only http and https URLs are permitted")
    if parsed.username or parsed.password:
        raise PermissionDenied("URLs with embedded credentials are refused")
    host = parsed.hostname
    if not host:
        raise ValidationFailure("url has no hostname")
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValidationFailure("url has an invalid port") from exc
    if port not in ALLOWED_PORTS:
        raise PermissionDenied(f"port {port} is not permitted for web fetch")

    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        addresses.append(ipaddress.ip_address(host))
    except ValueError:
        try:
            infos = resolver(host, port, type=socket.SOCK_STREAM)
        except (socket.gaierror, OSError) as exc:
            raise ToolExecutionError(f"could not resolve host: {host}") from exc
        for info in infos:
            try:
                addresses.append(ipaddress.ip_address(info[4][0]))
            except (ValueError, IndexError):
                continue
    if not addresses:
        raise ToolExecutionError("host did not resolve to an IP address")
    for address in addresses:
        if not address.is_global:
            raise PermissionDenied(
                f"refusing to fetch non-public address ({address}); SSRF protection is active"
            )
    return [str(address) for address in addresses]


def html_to_text(raw: str) -> str:
    without_scripts = _SCRIPT_BLOCKS.sub(" ", raw)
    replaced = re.sub(r"(?i)<(br|/p|/div|/li|/h[1-6])[^>]*>", "\n", without_scripts)
    text = _TAGS.sub(" ", replaced)
    text = html_module.unescape(text)
    return _BLANKS.sub("\n\n", text).strip()


def extract_title(raw: str) -> str | None:
    match = _TITLE.search(raw)
    return html_module.unescape(match.group(1)).strip()[:200] if match else None


class FetchPublicUrlInput(StrictModel):
    url: str = Field(min_length=8, max_length=2000)


class FetchPublicUrlTool(Tool):
    name = "web.fetch_public"
    description = "Fetch a public web page as untrusted text for research and summarisation."
    permission_scope = "web.read"
    risk = Risk.MEDIUM
    idempotent = True
    returns_untrusted = True
    timeout_seconds = 25.0
    max_output_bytes = 40_000
    input_model = FetchPublicUrlInput

    def run_sync(self, args: FetchPublicUrlInput, ctx: ToolContext) -> dict[str, Any]:
        if not ctx.config.allow_web_fetch:
            raise ToolUnavailable("web fetch is disabled by configuration")
        resolved = resolve_and_validate(args.url)
        limits = httpx.Limits(max_connections=2, max_keepalive_connections=0)
        headers = {"User-Agent": "MATRIX/0.1 (+local-first assistant)", "Accept": "text/html,text/plain"}
        with httpx.Client(
            timeout=httpx.Timeout(15.0, connect=8.0),
            follow_redirects=False,
            limits=limits,
            headers=headers,
        ) as client:
            with client.stream("GET", args.url) as response:
                status = response.status_code
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                location = response.headers.get("location")
                if status != 200:
                    return {
                        "url": args.url,
                        "status_code": status,
                        "location": location,
                        "text": "",
                        "content_type": content_type,
                        "untrusted": True,
                        "injection_markers": [],
                    }
                if content_type and not content_type.startswith(ALLOWED_CONTENT_TYPES):
                    raise ToolExecutionError(f"unsupported content type: {content_type}")
                chunks: list[bytes] = []
                total = 0
                limit = ctx.config.web_fetch_max_bytes
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total >= limit:
                        break
        raw = b"".join(chunks)[:limit].decode("utf-8", errors="replace")
        text = html_to_text(raw) if "html" in content_type or "<html" in raw[:2000].lower() else raw.strip()
        markers = detect_injection(text)
        return {
            "url": args.url,
            "status_code": status,
            "content_type": content_type,
            "title": extract_title(raw),
            "text": self.truncate(text),
            "characters": len(text),
            "resolved_addresses": resolved,
            "untrusted": True,
            "suspicious": bool(markers),
            "injection_markers": markers,
        }

    def verify(self, args: FetchPublicUrlInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = output.get("status_code") == 200 and bool(output.get("text"))
        return VerificationEvidence(
            ok=ok,
            confidence=0.8 if ok else 0.0,
            checks=["http_200_with_text" if ok else f"http_{output.get('status_code')}"],
            detail={"characters": output.get("characters", 0), "suspicious": output.get("suspicious", False)},
        )
