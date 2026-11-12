"""A15 - OCR extracts known text within tolerance (environment permitting)."""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.acceptance
pytest.importorskip("pytesseract", reason="OCR needs the optional vision extra")
pytest.importorskip("PIL", reason="OCR needs Pillow")
if shutil.which("tesseract") is None:  # pragma: no cover - environment dependent
    pytest.skip("Tesseract OCR binary is not installed", allow_module_level=True)

from conftest import execute_tool, wait_for_task  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def _make_image(path):
    image = Image.new("RGB", (520, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 60), "MATRIX OCR TEST 12345", fill="black")
    image.save(path)


async def test_ocr_extracts_known_text(client, runtime):
    target = runtime.config.workspace_dir / "ocr_sample.png"
    _make_image(target)

    task = await execute_tool(client, "vision.ocr_image", {"path": "ocr_sample.png"})
    done = await wait_for_task(client, task["id"])
    assert done["state"] == "COMPLETE", done["error"]
    output = done["tool_calls"][-1]["output"]
    text = output["text"].upper().replace(" ", "")
    assert "MATRIX" in text
    assert "12345" in text
    assert output["approximate"] is True
