"""Scoped filesystem tools.

Every path is resolved and checked against the approved roots *before* use, so
``..`` traversal and symlink escapes are refused server-side. Destructive or
overwriting operations carry a high risk class and therefore need approval.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from ..core.models import Risk, VerificationEvidence
from ..errors import ConflictError, ToolExecutionError
from .base import StrictModel, Tool, ToolContext

RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ScopedFileTool(Tool):
    permission_scope = "files.read"
    idempotent = True

    def check_name(self, path: Path) -> None:
        if path.name.upper() in RESERVED_NAMES:
            raise ToolExecutionError("reserved device name is not a valid file target")


class ListFilesInput(StrictModel):
    path: str = Field(default=".", max_length=1000)
    limit: int = Field(default=100, ge=1, le=1000)


class ListFilesTool(ScopedFileTool):
    name = "files.list"
    description = "List files and folders inside an approved workspace root."
    risk = Risk.LOW
    timeout_seconds = 15.0
    input_model = ListFilesInput

    def run_sync(self, args: ListFilesInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.resolve_path(args.path, must_exist=True)
        if not target.is_dir():
            raise ToolExecutionError("path is not a directory")
        entries: list[dict[str, Any]] = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if len(entries) >= min(args.limit, ctx.config.max_listing_entries):
                break
            try:
                stat = child.stat()
            except OSError:
                continue
            entries.append(
                {
                    "name": child.name,
                    "is_dir": child.is_dir(),
                    "size_bytes": stat.st_size,
                    "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
                }
            )
        return {"path": str(target), "entries": entries, "count": len(entries), "truncated": len(entries) >= args.limit}

    def verify(self, args: ListFilesInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("entries"), list) and output.get("path")
        return VerificationEvidence(ok=bool(ok), confidence=0.9 if ok else 0.0, checks=["listing_read"])


class ReadTextInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    max_bytes: int = Field(default=200_000, ge=1, le=1_000_000)


class ReadTextTool(ScopedFileTool):
    name = "files.read_text"
    description = "Read a UTF-8 text file inside an approved workspace root."
    risk = Risk.LOW
    timeout_seconds = 15.0
    input_model = ReadTextInput

    def run_sync(self, args: ReadTextInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.resolve_path(args.path, must_exist=True)
        if not target.is_file():
            raise ToolExecutionError("path is not a regular file")
        size = _size(target)
        limit = min(args.max_bytes, ctx.config.max_file_bytes)
        raw = target.read_bytes()[:limit]
        text = raw.decode("utf-8", errors="replace")
        return {
            "path": str(target),
            "content": text,
            "bytes_read": len(raw),
            "truncated": size > len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }

    def verify(self, args: ReadTextInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = isinstance(output.get("content"), str) and bool(output.get("sha256"))
        return VerificationEvidence(
            ok=ok,
            confidence=0.95 if ok else 0.0,
            checks=["content_hash_present" if ok else "content_missing"],
            detail={"bytes_read": output.get("bytes_read"), "truncated": output.get("truncated")},
        )


class WriteTextInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(max_length=1_000_000)
    overwrite: bool = False


class WriteTextTool(ScopedFileTool):
    name = "files.write_text"
    description = "Create a text file (or overwrite with explicit approval) inside an approved root."
    permission_scope = "files.write"
    risk = Risk.MEDIUM
    idempotent = False
    timeout_seconds = 20.0
    input_model = WriteTextInput

    def risk_for(self, args: WriteTextInput, ctx: ToolContext | None = None) -> Risk:
        if args.overwrite:
            return Risk.HIGH
        if ctx is not None:
            try:
                target = ctx.resolve_path(args.path)
            except Exception:
                # A path outside the approved roots is a permission refusal, not a
                # reason to ask the owner for permission to write there.
                return self.risk
            if target.exists():
                return Risk.HIGH
        return self.risk

    def run_sync(self, args: WriteTextInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.resolve_path(args.path)
        self.check_name(target)
        if target.exists():
            if target.is_dir():
                raise ToolExecutionError("target is a directory")
            if not args.overwrite:
                raise ConflictError(
                    f"file already exists: {target.name}. Overwrite requires explicit approval.",
                )
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=".matrix-", dir=str(target.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
                stream.write(args.content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, target)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
        return {
            "path": str(target),
            "bytes_written": len(args.content.encode("utf-8")),
            "sha256": _sha256(target),
            "overwrote": bool(args.overwrite),
        }

    def verify(self, args: WriteTextInput, output: dict[str, Any]) -> VerificationEvidence:
        target = Path(str(output.get("path", "")))
        expected = hashlib.sha256(args.content.encode("utf-8")).hexdigest()
        actual = _sha256(target) if target.is_file() else None
        ok = bool(actual) and actual == expected
        return VerificationEvidence(
            ok=ok,
            confidence=1.0 if ok else 0.0,
            checks=["read_back_hash_matches" if ok else "read_back_hash_mismatch"],
            detail={"expected": expected[:16], "actual": (actual or "")[:16]},
        )


class DeleteFileInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)


class DeleteFileTool(ScopedFileTool):
    name = "files.delete"
    description = "Delete a single file or empty directory inside an approved root. Requires approval."
    permission_scope = "files.write"
    risk = Risk.HIGH
    idempotent = False
    timeout_seconds = 15.0
    input_model = DeleteFileInput

    def run_sync(self, args: DeleteFileInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.resolve_path(args.path, must_exist=True)
        if target.is_dir():
            if any(target.iterdir()):
                raise ToolExecutionError("refusing to recursively delete a non-empty directory")
            target.rmdir()
        else:
            target.unlink()
        return {"path": str(target), "deleted": True, "existed": True}

    def verify(self, args: DeleteFileInput, output: dict[str, Any]) -> VerificationEvidence:
        target = Path(str(output.get("path", "")))
        gone = not target.exists()
        return VerificationEvidence(
            ok=gone,
            confidence=1.0 if gone else 0.0,
            checks=["path_absent" if gone else "path_still_exists"],
        )


class MovePathInput(StrictModel):
    source: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)
    overwrite: bool = False


class MovePathTool(ScopedFileTool):
    name = "files.move"
    description = "Move or rename a file inside approved roots. Overwriting requires approval."
    permission_scope = "files.write"
    risk = Risk.MEDIUM
    idempotent = False
    timeout_seconds = 20.0
    input_model = MovePathInput

    def risk_for(self, args: MovePathInput, ctx: ToolContext | None = None) -> Risk:
        if args.overwrite:
            return Risk.HIGH
        if ctx is not None:
            try:
                destination = ctx.resolve_path(args.destination)
            except Exception:
                return self.risk
            if destination.exists():
                return Risk.HIGH
        return self.risk

    def run_sync(self, args: MovePathInput, ctx: ToolContext) -> dict[str, Any]:
        source = ctx.resolve_path(args.source, must_exist=True)
        destination = ctx.resolve_path(args.destination)
        self.check_name(destination)
        if destination.exists() and not args.overwrite:
            raise ConflictError("destination exists; overwrite requires explicit approval")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if args.overwrite and destination.exists():
            if destination.is_dir():
                raise ToolExecutionError("refusing to replace a directory")
            destination.unlink()
        shutil.move(str(source), str(destination))
        return {"source": str(source), "destination": str(destination), "moved": True}

    def verify(self, args: MovePathInput, output: dict[str, Any]) -> VerificationEvidence:
        source = Path(str(output.get("source", "")))
        destination = Path(str(output.get("destination", "")))
        ok = destination.exists() and not source.exists()
        return VerificationEvidence(
            ok=ok,
            confidence=1.0 if ok else 0.0,
            checks=["destination_exists", "source_absent"] if ok else ["move_verification_failed"],
        )


class StatPathInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)


class StatPathTool(ScopedFileTool):
    name = "files.stat"
    description = "Read metadata (size, timestamps, type) for a path inside an approved root."
    risk = Risk.LOW
    timeout_seconds = 10.0
    input_model = StatPathInput

    def run_sync(self, args: StatPathInput, ctx: ToolContext) -> dict[str, Any]:
        target = ctx.resolve_path(args.path, must_exist=True)
        stat = target.stat()
        return {
            "path": str(target),
            "is_file": target.is_file(),
            "is_dir": target.is_dir(),
            "size_bytes": stat.st_size,
            "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(stat.st_mtime)),
        }

    def verify(self, args: StatPathInput, output: dict[str, Any]) -> VerificationEvidence:
        ok = bool(output.get("path")) and isinstance(output.get("size_bytes"), int)
        return VerificationEvidence(ok=ok, confidence=0.9 if ok else 0.0, checks=["stat_read"])


__all__ = [
    "DeleteFileTool",
    "ListFilesTool",
    "MovePathTool",
    "ReadTextTool",
    "StatPathTool",
    "WriteTextTool",
]
