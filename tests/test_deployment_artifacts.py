"""Unit tests verifying production deployment artifacts (Dockerfile, Compose, .dockerignore, documentation)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_security_posture():
    dockerfile = ROOT / "Dockerfile"
    assert dockerfile.exists(), "Dockerfile must exist at repository root"
    content = dockerfile.read_text(encoding="utf-8")

    assert "USER matrix:matrix" in content, "Dockerfile must run as non-root user"
    assert "HEALTHCHECK" in content, "Dockerfile must declare a HEALTHCHECK"
    assert "/api/health" in content, "HEALTHCHECK must verify /api/health"
    assert 'VOLUME ["/data"]' in content, "Dockerfile must declare /data persistent volume"
    assert "EXPOSE 8765" in content, "Dockerfile must expose port 8765"
    assert "STOPSIGNAL SIGTERM" in content, "Dockerfile must declare graceful SIGTERM stopsignal"
    assert "MATRIX_ALLOW_WEB_FETCH=false" in content, "Web fetch must remain disabled by default"
    assert "MATRIX_ALLOW_CLOUD_MODELS=false" in content, "Cloud models must remain disabled by default"

    # Verify no hardcoded secrets exist
    assert "123456789:" not in content
    assert "token" not in content.lower() or "pip install" in content


def test_dockerignore_excludes_secrets_and_runtime_data():
    dockerignore = ROOT / ".dockerignore"
    assert dockerignore.exists(), ".dockerignore must exist at repository root"
    content = dockerignore.read_text(encoding="utf-8")

    assert ".env" in content
    assert "secrets" in content
    assert ".git" in content
    assert "*.db" in content
    assert "*.sqlite" in content
    assert "credentials.json" in content
    assert "token.json" in content
    assert "owner_token.txt" in content


def test_docker_compose_loopback_and_persistence():
    compose = ROOT / "docker-compose.yml"
    assert compose.exists(), "docker-compose.yml must exist at repository root"
    content = compose.read_text(encoding="utf-8")

    # Critical security rule: loopback binding on host only, NEVER 0.0.0.0
    assert '"127.0.0.1:8765:8765"' in content or "'127.0.0.1:8765:8765'" in content
    assert "0.0.0.0:8765:8765" not in content

    # Persistence
    assert "matrix-data:/data" in content
    assert "unless-stopped" in content
    assert "stop_grace_period: 30s" in content
    assert "MATRIX_ALLOW_WEB_FETCH=false" in content
    assert "MATRIX_ALLOW_CLOUD_MODELS=false" in content


def test_deployment_documentation_completeness():
    doc = ROOT / "docs" / "DEPLOYMENT.md"
    assert doc.exists(), "docs/DEPLOYMENT.md must exist"
    content = doc.read_text(encoding="utf-8")

    assert "Tailscale" in content
    assert "Backup & Restore" in content
    assert "Upgrade & Rollback" in content
    assert "Real-World Production Certification Notice" in content
    assert "Category A" in content
    assert "Category B" in content
