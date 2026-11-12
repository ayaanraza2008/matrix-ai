"""Authentication, device pairing and session revocation.

Every endpoint except /api/health and /api/auth/pair requires a bearer token.
Tokens and pairing codes are stored only as SHA-256 hashes; device revocation
invalidates its sessions immediately.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from ..config import MatrixConfig
from ..core.models import (
    DeviceView,
    PairingCodeView,
    PairResponse,
    Principal,
    iso,
    parse_dt,
    utcnow,
)
from ..errors import AuthenticationError, NotFoundError
from ..storage.store import Store

PAIRING_TTL_SECONDS = 300
SESSION_TTL_DAYS = 30
OWNER_SESSION_TTL_DAYS = 365


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, store: Store, config: MatrixConfig) -> None:
        self.store = store
        self.config = config

    # -- bootstrap --------------------------------------------------------
    def bootstrap(self) -> None:
        """Register the local owner and the owner token (hash only in the DB)."""
        self.store.ensure_user("owner", "Owner", "owner")
        if not self.config.owner_token:
            return
        expires = iso(utcnow() + timedelta(days=OWNER_SESSION_TTL_DAYS))
        token_hash = hash_secret(self.config.owner_token)
        existing = self.store.db.query_one("SELECT token_hash FROM sessions WHERE token_hash=?", (token_hash,))
        if existing is None:
            self.store.db.execute(
                "INSERT INTO sessions(token_hash, user_id, device_id, scope, created_at, expires_at) "
                "VALUES(?,?,?,?,?,?)",
                (token_hash, "owner", None, "owner", iso(), expires),
            )
            self.store.audit("auth.owner_session_created", actor="system", status="ok")
        else:
            self.store.db.execute(
                "UPDATE sessions SET expires_at=?, revoked_at=NULL WHERE token_hash=?", (expires, token_hash)
            )

    # -- verification -----------------------------------------------------
    def verify_token(self, token: str | None) -> Principal:
        if not token:
            raise AuthenticationError("missing bearer token")
        token_hash = hash_secret(token)
        row = self.store.db.query_one("SELECT * FROM sessions WHERE token_hash=?", (token_hash,))
        if row is None:
            raise AuthenticationError("invalid token")
        if row["revoked_at"]:
            raise AuthenticationError("token revoked")
        expiry = parse_dt(row["expires_at"])
        if expiry is None or expiry <= utcnow():
            raise AuthenticationError("token expired")
        if row["device_id"]:
            device = self.store.db.query_one("SELECT * FROM devices WHERE id=?", (row["device_id"],))
            if device is None or device["revoked_at"]:
                raise AuthenticationError("device revoked")
            self.store.db.execute(
                "UPDATE devices SET last_seen_at=? WHERE id=?", (iso(), row["device_id"])
            )
        self.store.db.execute("UPDATE sessions SET last_used_at=? WHERE token_hash=?", (iso(), token_hash))
        return Principal(
            user_id=row["user_id"],
            device_id=row["device_id"],
            scope=row["scope"],
            token_id=token_hash[:12],
            is_owner=row["scope"] == "owner",
        )

    # -- sessions and pairing --------------------------------------------
    def create_session(
        self, *, user_id: str, device_id: str | None = None, ttl_days: int = SESSION_TTL_DAYS
    ) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        expires = iso(utcnow() + timedelta(days=ttl_days))
        self.store.db.execute(
            "INSERT INTO sessions(token_hash, user_id, device_id, scope, created_at, expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (hash_secret(token), user_id, device_id, "owner", iso(), expires),
        )
        return token, expires

    def issue_pairing_code(self, principal: Principal) -> PairingCodeView:
        if not principal.is_owner:
            raise AuthenticationError("only the owner may issue pairing codes")
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = iso(utcnow() + timedelta(seconds=PAIRING_TTL_SECONDS))
        self.store.db.execute(
            "INSERT INTO pairing_codes(code_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
            (hash_secret(code), principal.user_id, iso(), expires),
        )
        self.store.audit(
            "auth.pairing_code_issued",
            actor=principal.user_id,
            status="ok",
            detail={"expires_at": expires},
        )
        return PairingCodeView(code=code, expires_at=expires)

    def pair(self, *, code: str, device_name: str, platform: str) -> PairResponse:
        row = self.store.db.query_one("SELECT * FROM pairing_codes WHERE code_hash=?", (hash_secret(code),))
        if row is None:
            raise AuthenticationError("invalid pairing code")
        if row["used_at"]:
            raise AuthenticationError("pairing code already used")
        expires = parse_dt(row["expires_at"])
        if expires is None or expires <= utcnow():
            raise AuthenticationError("pairing code expired")
        device_id = secrets.token_hex(8)
        token, expires_at = self.create_session(user_id=row["user_id"], device_id=device_id)
        now = iso()
        self.store.db.execute(
            "INSERT INTO devices(id, user_id, name, platform, key_hash, paired_at, last_seen_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (device_id, row["user_id"], device_name[:80], platform, hash_secret(token), now, now),
        )
        self.store.db.execute(
            "UPDATE pairing_codes SET used_at=? WHERE code_hash=?", (now, hash_secret(code))
        )
        self.store.audit(
            "auth.device_paired",
            actor=row["user_id"],
            status="ok",
            detail={"device_id": device_id, "platform": platform, "device_name": device_name[:80]},
        )
        return PairResponse(token=token, device_id=device_id, user_id=row["user_id"], expires_at=expires_at)

    def list_devices(self) -> list[DeviceView]:
        rows = self.store.db.query("SELECT * FROM devices ORDER BY paired_at DESC")
        return [
            DeviceView(
                id=row["id"],
                name=row["name"],
                platform=row["platform"],
                paired_at=row["paired_at"],
                last_seen_at=row["last_seen_at"],
                revoked=bool(row["revoked_at"]),
            )
            for row in rows
        ]

    def revoke_device(self, device_id: str, *, actor: str = "owner") -> None:
        row = self.store.db.query_one("SELECT * FROM devices WHERE id=?", (device_id,))
        if row is None:
            raise NotFoundError(f"device {device_id} not found")
        now = iso()
        self.store.db.execute("UPDATE devices SET revoked_at=? WHERE id=?", (now, device_id))
        self.store.db.execute("UPDATE sessions SET revoked_at=? WHERE device_id=?", (now, device_id))
        self.store.audit(
            "auth.device_revoked",
            actor=actor,
            status="ok",
            detail={"device_id": device_id, "device_name": row["name"]},
        )

    def cleanup_expired(self) -> int:
        now = iso()
        codes = self.store.db.execute(
            "DELETE FROM pairing_codes WHERE expires_at <= ? OR used_at IS NOT NULL", (now,)
        ).rowcount
        sessions = self.store.db.execute(
            "DELETE FROM sessions WHERE expires_at <= ? OR revoked_at IS NOT NULL", (now,)
        ).rowcount
        return int(codes or 0) + int(sessions or 0)
