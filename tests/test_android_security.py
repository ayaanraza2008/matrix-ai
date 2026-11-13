from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
ROOT = Path(__file__).resolve().parents[1] / "android" / "app" / "src"


def test_release_manifest_rejects_cleartext_traffic_and_uses_network_policy():
    manifest = ET.parse(ROOT / "main" / "AndroidManifest.xml").getroot()
    application = manifest.find("application")
    assert application is not None
    assert application.get(f"{ANDROID_NS}usesCleartextTraffic") == "false"
    assert application.get(f"{ANDROID_NS}networkSecurityConfig") == "@xml/network_security_config"


def test_debug_network_policy_allows_only_emulator_and_loopback_http():
    policy = ET.parse(ROOT / "debug" / "res" / "xml" / "network_security_config.xml").getroot()
    base = policy.find("base-config")
    assert base is not None
    assert base.get("cleartextTrafficPermitted") == "false"
    domain_configs = policy.findall("domain-config")
    assert len(domain_configs) == 1
    domain_config = domain_configs[0]
    assert domain_config.get("cleartextTrafficPermitted") == "true"
    domains = {node.text for node in domain_config.findall("domain")}
    assert domains == {"10.0.2.2", "localhost", "127.0.0.1"}


def test_android_pairing_token_is_not_saved_in_instance_state():
    activity = ROOT / "main" / "java" / "com" / "ayaanraza" / "matrix" / "MainActivity.kt"
    source = activity.read_text(encoding="utf-8")
    assert "var token by remember { mutableStateOf<String?>(null) }" in source
    assert "var token by rememberSaveable" not in source
