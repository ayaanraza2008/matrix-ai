"""Secret scanning for the repository (runs in CI and before push).

Findings intentionally report the *location* and pattern name, never the secret
value, so a scan log cannot leak what it detected.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai-like-key", re.compile(r"\bsk-[A-Za-z0-9_\-]{24,}\b")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("github-fine-grained", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private-key-block", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:[A-Za-z0-9_\-]{30,}\b")),
    ("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}\b")),
    (
        "hardcoded-secret-assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|secret|password|passwd|auth[_-]?token|access[_-]?token)\b"
            r"\s*[:=]\s*[\"'][A-Za-z0-9_\-/+.]{20,}[\"']"
        ),
    ),
)

EXCLUDED_DIRS = {
    ".git",
    # Vendored prompt corpus contains intentionally synthetic secret examples.
    "agency-agents",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "build",
    "dist",
    "htmlcov",
    ".gradle",
}
EXCLUDED_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".gguf",
    ".bin",
    ".onnx",
    ".pt",
    ".safetensors",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ico",
    ".zip",
    ".gz",
    ".apk",
    ".aab",
    ".jks",
    ".keystore",
    ".p12",
    ".pfx",
    ".pem",
    ".key",
}
#: These files contain the detector patterns themselves or synthetic fixtures.
ALLOWLISTED_FILES = {
    "secret_scan.py",
    "redaction.py",
    "test_a09_memory.py",
    "test_a22_secret_scan.py",
    ".env.example",
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    pattern: str
    excerpt: str


def scan_text(text: str, *, path: str = "<memory>") -> list[Finding]:
    findings: list[Finding] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if len(line) > 4000:
            line = line[:4000]
        for name, pattern in PATTERNS:
            if pattern.search(line):
                findings.append(
                    Finding(path=path, line=line_number, pattern=name, excerpt="[redacted]")
                )
                break
    return findings


def scan_repository(root: Path | str) -> list[Finding]:
    root = Path(root).resolve()
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if any(part == "tests" for part in relative.parts):
            continue
        if path.name in ALLOWLISTED_FILES:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        findings.extend(scan_text(text, path=str(relative).replace("\\", "/")))
    return findings


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    root = Path(args[0]).resolve() if args else Path(__file__).resolve().parents[3]
    findings = scan_repository(root)
    if not findings:
        print(f"secret scan: clean ({root})")
        return 0
    print(f"secret scan: {len(findings)} potential secret(s) found")
    for finding in findings:
        print(f"  {finding.path}:{finding.line}  [{finding.pattern}]")
    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
