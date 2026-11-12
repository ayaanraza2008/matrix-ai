"""OCR tool (optional dependency).

OCR is approximate and the evidence says so. When Tesseract or the optional
Python packages are missing the tool raises ``ToolUnavailable`` instead of
pretending it read the image.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import ToolExecutionError, ToolUnavailable
from .base import StrictModel, Tool, ToolContext

MAX_IMAGE_BYTES = 12_000_000


class OcrImageInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    language: str = Field(default="eng", min_length=2, max_length=20, pattern=r"^[a-zA-Z+]+$")


class OcrImageTool(Tool):
    name = "vision.ocr_image"
    description = "Extract text from an image inside an approved root using local Tesseract OCR."
    permission_scope = "vision.read"
    risk = Risk.MEDIUM
    idempotent = True
    timeout_seconds = 45.0
    input_model = OcrImageInput

    def run_sync(self, args: OcrImageInput, ctx: ToolContext) -> dict[str, Any]:
        try:
            import pytesseract  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ToolUnavailable(
                "OCR needs optional dependencies: pip install 'matrix-ai[vision]' and Tesseract OCR"
            ) from exc

        target = ctx.resolve_path(args.path, must_exist=True)
        if target.stat().st_size > MAX_IMAGE_BYTES:
            raise ToolExecutionError("image is larger than the 12 MB safety limit")
        try:
            with Image.open(target) as image:
                text = pytesseract.image_to_string(image, lang=args.language)
        except pytesseract.TesseractNotFoundError as exc:
            raise ToolUnavailable("the Tesseract OCR binary is not installed on this PC") from exc
        except Exception as exc:  # pragma: no cover - corrupted image paths
            raise ToolExecutionError(f"OCR failed: {type(exc).__name__}") from exc
        return {
            "path": str(target),
            "text": self.truncate((text or "").strip()),
            "characters": len((text or "").strip()),
            "language": args.language,
            "approximate": True,
        }

    def verify(self, args: OcrImageInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("text"), str) and isinstance(output.get("characters"), int)
        return VerificationEvidence(
            ok=ok,
            confidence=0.7 if ok else 0.0,
            checks=["ocr_completed_approximate" if ok else "ocr_failed"],
            detail={"characters": output.get("characters", 0), "engine": "tesseract"},
        )


__all__ = ["OcrImageTool"]
