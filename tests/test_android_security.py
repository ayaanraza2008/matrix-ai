from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
ROOT = Path(__file__).resolve().parents[1] / "android" / "app" / "src"


def _cleartext_setting(manifest: Path) -> str | None:
    root = ET.parse(manifest).getroot()
    application = root.find("application")
    assert application is not None
    return application.get(f"{ANDROID_NS}usesCleartextTraffic")


def test_release_manifest_rejects_cleartext_traffic():
    assert _cleartext_setting(ROOT / "main" / "AndroidManifest.xml") == "false"


def test_debug_manifest_is_the_only_cleartext_override():
    assert _cleartext_setting(ROOT / "debug" / "AndroidManifest.xml") == "true"


def test_android_pairing_token_is_not_saved_in_instance_state():
    activity = ROOT / "main" / "java" / "com" / "ayaanraza" / "matrix" / "MainActivity.kt"
    source = activity.read_text(encoding="utf-8")
    assert "var token by remember { mutableStateOf(tokenVault.load()) }" in source
    assert "var token by rememberSaveable" not in source


def test_android_paired_token_uses_keystore_encryption():
    vault = ROOT / "main" / "java" / "com" / "ayaanraza" / "matrix" / "TokenVault.kt"
    source = vault.read_text(encoding="utf-8")
    assert '"AndroidKeyStore"' in source
    assert '"AES/GCM/NoPadding"' in source
    assert "KeyGenParameterSpec.Builder" in source
    assert "GCMParameterSpec" in source
    assert "preferences.edit()" in source
    assert "Base64.encodeToString(encrypted, Base64.NO_WRAP)" in source


def test_android_ui_persists_and_can_clear_keystore_token():
    activity = ROOT / "main" / "java" / "com" / "ayaanraza" / "matrix" / "MainActivity.kt"
    source = activity.read_text(encoding="utf-8")
    assert "TokenVault(context.applicationContext)" in source
    assert "mutableStateOf(tokenVault.load())" in source
    assert "tokenVault.save(newToken)" in source
    assert "tokenVault.clear()" in source
    manifest = ROOT / "main" / "AndroidManifest.xml"
    assert 'android:allowBackup="false"' in manifest.read_text(encoding="utf-8")
