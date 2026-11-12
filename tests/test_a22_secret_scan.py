"""A22 - no credentials or private artifacts are present in the repository."""

from __future__ import annotations

from pathlib import Path

import pytest

from matrix_ai.security.secret_scan import scan_repository, scan_text

pytestmark = pytest.mark.acceptance

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_repository_is_free_of_committed_secrets():
    findings = scan_repository(REPO_ROOT)
    assert findings == [], [(finding.path, finding.line, finding.pattern) for finding in findings]


def test_scanner_detects_a_planted_secret():
    # Built at runtime so this test file itself never contains a token literal.
    planted = "api_" + "key = '" + ("A" * 12) + "b" * 12 + "'"
    findings = scan_text(planted, path="planted.py")
    assert findings, "scanner failed to detect a hard-coded secret assignment"
    assert findings[0].pattern == "hardcoded-secret-assignment"
    assert findings[0].excerpt == "[redacted]"


def test_scan_excludes_synthetic_tests_directory():
    # The suite itself contains injection payloads and fake tokens by design.
    findings = scan_repository(REPO_ROOT / "tests")
    assert findings == []


def test_gitignore_covers_secret_and_runtime_paths():
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "*.db", "*.gguf", "owner_token", "*.keystore"):
        assert pattern in gitignore
