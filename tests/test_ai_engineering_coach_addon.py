from matrix_ai.integrations.addon_catalog import ADDON_BY_ID


def test_ai_engineering_coach_is_registered_as_source_only_guidance():
    addon = ADDON_BY_ID["ai-engineering-coach"]
    assert addon["repository"] == "microsoft/AI-Engineering-Coach"
    assert addon["protocol"] == "skill_pack"
    assert addon["context_enabled"] is True
    assert addon["license"] == "MIT"
    assert addon["commit"] == "c5cfba17e732f100b4322f5c9c2116063d7e4ac4"


def test_ai_engineering_coach_does_not_claim_a_headless_runtime():
    addon = ADDON_BY_ID["ai-engineering-coach"]
    assert addon["kind"] == "engineering_analytics_reference"
    assert addon["protocol"] != "mcp_stdio"
    assert addon["protocol"] != "http_service"
    assert addon["risk"] == "low"


def test_ai_engineering_coach_source_is_within_lazy_install_limit():
    addon = ADDON_BY_ID["ai-engineering-coach"]
    assert addon["size_mb"] <= 200
    assert "README.md" in addon["sparse_paths"]
    assert "docs/**" in addon["sparse_paths"]
