"""Governed memory: store, retrieve, correct, export and delete.

Secrets are refused at the boundary; every item carries provenance, confidence
and retention metadata. Full-text search is an *index* - the SQLite row remains
the source of truth.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from ..core.models import MemoryKind, MemoryView, iso, parse_dt, utcnow
from ..errors import NotFoundError, PermissionDenied, ValidationFailure
from ..security.redaction import contains_secret
from ..storage.db import Database


def _tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[\w@./+-]{2,}", query, flags=re.UNICODE)][:8]


class MemoryService:
    def __init__(self, db: Database, user_id: str = "owner") -> None:
        self.db = db
        self.user_id = user_id

    # -- validation -------------------------------------------------------
    @staticmethod
    def _validate_text(title: str, content: str) -> tuple[str, str]:
        title = (title or "").strip()
        content = (content or "").strip()
        if not title or not content:
            raise ValidationFailure("memory title and content are required")
        if len(title) > 200 or len(content) > 20_000:
            raise ValidationFailure("memory item is too large")
        if contains_secret(title) or contains_secret(content):
            raise PermissionDenied(
                "MATRIX never stores credentials in memory; keep secrets in the OS secret store"
            )
        return title, content

    # -- writes -----------------------------------------------------------
    def add(
        self,
        *,
        kind: MemoryKind | str = MemoryKind.KNOWLEDGE,
        title: str,
        content: str,
        source: str = "user",
        confidence: float = 1.0,
        expires_at: datetime | None = None,
    ) -> MemoryView:
        title, content = self._validate_text(title, content)
        try:
            kind_value = MemoryKind(kind).value
        except ValueError as exc:
            raise ValidationFailure(f"unknown memory kind: {kind}") from exc
        item_id = uuid.uuid4().hex
        now = iso()
        expires = iso(expires_at) if expires_at else None
        self.db.execute(
            "INSERT INTO memory_items(id, user_id, kind, title, content, source, confidence, "
            "created_at, updated_at, expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                item_id,
                self.user_id,
                kind_value,
                title,
                content,
                (source or "user")[:500],
                max(0.0, min(1.0, float(confidence))),
                now,
                now,
                expires,
            ),
        )
        self._index(item_id, title, content)
        return self.get(item_id)

    def update(self, item_id: str, *, title: str | None = None, content: str | None = None) -> MemoryView:
        current = self.get(item_id)
        title = current.title if title is None else title
        content = current.content if content is None else content
        title, content = self._validate_text(title, content)
        self.db.execute(
            "UPDATE memory_items SET title=?, content=?, updated_at=? WHERE id=? AND deleted_at IS NULL",
            (title, content, iso(), item_id),
        )
        self._index(item_id, title, content)
        return self.get(item_id)

    def delete(self, item_id: str) -> None:
        row = self.db.query_one(
            "SELECT id FROM memory_items WHERE id=? AND deleted_at IS NULL", (item_id,)
        )
        if row is None:
            raise NotFoundError(f"memory item {item_id} not found")
        self.db.execute("UPDATE memory_items SET deleted_at=?, updated_at=? WHERE id=?", (iso(), iso(), item_id))
        self._deindex(item_id)

    # -- reads ------------------------------------------------------------
    def get(self, item_id: str) -> MemoryView:
        row = self.db.query_one("SELECT * FROM memory_items WHERE id=?", (item_id,))
        if row is None or row["deleted_at"]:
            raise NotFoundError(f"memory item {item_id} not found")
        return self._view(row)

    def list(self, *, limit: int = 50, kind: MemoryKind | str | None = None) -> list[MemoryView]:
        limit = max(1, min(limit, 200))
        if kind:
            rows = self.db.query(
                "SELECT * FROM memory_items WHERE deleted_at IS NULL AND user_id=? AND kind=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (self.user_id, MemoryKind(kind).value, limit),
            )
        else:
            rows = self.db.query(
                "SELECT * FROM memory_items WHERE deleted_at IS NULL AND user_id=? "
                "ORDER BY updated_at DESC LIMIT ?",
                (self.user_id, limit),
            )
        return [self._view(row) for row in rows]

    def search(
        self,
        query: str,
        *,
        kinds: Sequence[MemoryKind | str] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        limit = max(1, min(limit, 20))
        expired_before = iso(utcnow())
        kind_values = [MemoryKind(kind).value for kind in (kinds or [])]
        kind_clause = ""
        kind_params: list[Any] = []
        if kind_values:
            placeholders = ",".join("?" for _ in kind_values)
            kind_clause = f" AND m.kind IN ({placeholders})"
            kind_params = kind_values

        rows: list[Any]
        tokens = _tokens(query)
        if getattr(self.db, "fts_enabled", False) and tokens:
            match_expression = " OR ".join(f'"{token}"' for token in tokens)
            try:
                rows = self.db.query(
                    "SELECT m.*, bm25(memory_fts) AS rank FROM memory_fts "
                    "JOIN memory_items m ON m.id = memory_fts.id "
                    "WHERE memory_fts MATCH ? AND m.deleted_at IS NULL AND m.user_id=? "
                    "AND (m.expires_at IS NULL OR m.expires_at > ?)"
                    + kind_clause
                    + " ORDER BY bm25(memory_fts) LIMIT ?",
                    (match_expression, self.user_id, expired_before, *kind_params, limit),
                )
            except Exception:  # pragma: no cover - malformed MATCH falls back to LIKE
                rows = []
        else:
            rows = []

        if not rows:
            like = f"%{query}%"
            rows = self.db.query(
                "SELECT m.*, NULL AS rank FROM memory_items m WHERE m.deleted_at IS NULL AND m.user_id=? "
                "AND (m.expires_at IS NULL OR m.expires_at > ?) "
                "AND (m.title LIKE ? OR m.content LIKE ?)" + kind_clause + " ORDER BY m.updated_at DESC LIMIT ?",
                (self.user_id, expired_before, like, like, *kind_params, limit),
            )

        results: list[dict[str, Any]] = []
        for row in rows:
            rank = row["rank"] if "rank" in row.keys() else None
            results.append(
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "title": row["title"],
                    "content": row["content"],
                    "source": row["source"],
                    "confidence": row["confidence"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "expires_at": row["expires_at"],
                    "score": round(-float(rank), 4) if rank is not None else None,
                }
            )
        return results

    def export(self) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM memory_items WHERE user_id=? AND deleted_at IS NULL ORDER BY kind, created_at",
            (self.user_id,),
        )
        return [self._view(row).model_dump(mode="json") for row in rows]

    def sweep_expired(self, now: datetime | None = None) -> int:
        now_iso = iso(now or utcnow())
        rows = self.db.query(
            "SELECT id FROM memory_items WHERE deleted_at IS NULL AND expires_at IS NOT NULL AND expires_at <= ?",
            (now_iso,),
        )
        for row in rows:
            self.db.execute(
                "UPDATE memory_items SET deleted_at=?, updated_at=? WHERE id=?", (now_iso, now_iso, row["id"])
            )
            self._deindex(row["id"])
        return len(rows)

    # -- internals --------------------------------------------------------
    def _index(self, item_id: str, title: str, content: str) -> None:
        if not getattr(self.db, "fts_enabled", False):
            return
        self.db.execute("DELETE FROM memory_fts WHERE id=?", (item_id,))
        self.db.execute("INSERT INTO memory_fts(id, title, content) VALUES(?,?,?)", (item_id, title, content))

    def _deindex(self, item_id: str) -> None:
        if getattr(self.db, "fts_enabled", False):
            self.db.execute("DELETE FROM memory_fts WHERE id=?", (item_id,))

    @staticmethod
    def _view(row) -> MemoryView:
        return MemoryView(
            id=row["id"],
            kind=MemoryKind(row["kind"]),
            title=row["title"],
            content=row["content"],
            source=row["source"],
            confidence=row["confidence"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            expires_at=row["expires_at"],
        )


def is_expired(item: MemoryView) -> bool:
    expiry = parse_dt(item.expires_at)
    return expiry is not None and expiry <= utcnow()
