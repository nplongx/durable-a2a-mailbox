"""SQLite-backed A2A mailbox. Authentication is supplied by the host application."""

from __future__ import annotations

import hashlib
import hmac
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class AuthenticatedPrincipal:
    """Identity asserted by a host after successful authentication."""

    subject: str

    def __post_init__(self) -> None:
        if not isinstance(self.subject, str) or not self.subject.strip() or len(self.subject) > 320:
            raise ValueError("principal subject must contain 1-320 characters")

    @property
    def normalized_subject(self) -> str:
        return self.subject.strip().casefold()


@dataclass(frozen=True, slots=True)
class MailboxLimits:
    """Resource limits for one mailbox database/tenant boundary."""

    max_endpoints_per_principal: int = 20
    max_pending_per_endpoint: int = 100
    default_ttl_seconds: int = 86_400
    max_ttl_seconds: int = 604_800

    def __post_init__(self) -> None:
        values = (
            ("max_endpoints_per_principal", self.max_endpoints_per_principal, 1, 100),
            ("max_pending_per_endpoint", self.max_pending_per_endpoint, 1, 1000),
            ("default_ttl_seconds", self.default_ttl_seconds, 60, 604_800),
            ("max_ttl_seconds", self.max_ttl_seconds, 60, 604_800),
        )
        for name, value, minimum, maximum in values:
            if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
        if self.default_ttl_seconds > self.max_ttl_seconds:
            raise ValueError("default_ttl_seconds must not exceed max_ttl_seconds")


class MailboxStore:
    """Durable A2A mailbox with caller-supplied authenticated principals.

    The database path is explicit and belongs to the host. The host must
    authenticate callers before constructing `AuthenticatedPrincipal` values.
    """

    def __init__(
        self,
        database_path: str | Path,
        *,
        identity_secret: str | bytes,
        limits: MailboxLimits | None = None,
    ) -> None:
        if not isinstance(database_path, (str, Path)) or not str(database_path).strip():
            raise ValueError("database_path must be an explicit filesystem path")
        self.database_path = Path(database_path).expanduser().resolve()
        if str(database_path) == ":memory:":
            raise ValueError("use a file-backed temporary database; each operation opens its own connection")
        if not self.database_path.parent.is_dir():
            raise FileNotFoundError(f"database parent directory does not exist: {self.database_path.parent}")
        secret = identity_secret.encode("utf-8") if isinstance(identity_secret, str) else identity_secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("identity_secret must contain at least 32 bytes")
        if limits is not None and not isinstance(limits, MailboxLimits):
            raise TypeError("limits must be a MailboxLimits instance")
        self._identity_secret = secret
        self.limits = limits if limits is not None else MailboxLimits()
        self._initialize_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=10)
        try:
            with db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA foreign_keys=ON")
                db.execute("PRAGMA journal_mode=WAL")
                yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            yield db

    def _initialize_schema(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """CREATE TABLE IF NOT EXISTS a2a_endpoints (
                    endpoint_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    owner_fingerprint TEXT NOT NULL,
                    label TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('active', 'disabled')),
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_endpoints_owner "
                "ON a2a_endpoints(owner_id, state, created_at)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS a2a_endpoint_acl (
                    endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                    sender_fingerprint TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(endpoint_id, sender_fingerprint)
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS a2a_endpoint_peer_acl_mode (
                    endpoint_id TEXT PRIMARY KEY REFERENCES a2a_endpoints(endpoint_id),
                    configured_at INTEGER NOT NULL
                )"""
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS a2a_endpoint_peer_acl (
                    endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                    sender_endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY(endpoint_id, sender_endpoint_id)
                )"""
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_peer_acl_sender "
                "ON a2a_endpoint_peer_acl(sender_endpoint_id)"
            )
            db.execute(
                """CREATE TABLE IF NOT EXISTS a2a_messages (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    sender_endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                    sender_owner_id TEXT NOT NULL,
                    sender_fingerprint TEXT NOT NULL,
                    recipient_endpoint_id TEXT NOT NULL REFERENCES a2a_endpoints(endpoint_id),
                    body TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('queued', 'received', 'acked', 'expired')),
                    idempotency_key TEXT NOT NULL,
                    reply_to_message_id TEXT REFERENCES a2a_messages(message_id),
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    received_at INTEGER,
                    acked_at INTEGER,
                    UNIQUE(sender_endpoint_id, idempotency_key)
                )"""
            )
            message_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(a2a_messages)").fetchall()
            }
            if "reply_to_message_id" not in message_columns:
                db.execute(
                    "ALTER TABLE a2a_messages ADD COLUMN reply_to_message_id TEXT "
                    "REFERENCES a2a_messages(message_id)"
                )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_messages_inbox "
                "ON a2a_messages(recipient_endpoint_id, state, seq)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_a2a_messages_expiry "
                "ON a2a_messages(expires_at, state)"
            )

    @staticmethod
    def _require_principal(principal: AuthenticatedPrincipal) -> str:
        if not isinstance(principal, AuthenticatedPrincipal):
            raise PermissionError("AuthenticatedPrincipal from the host is required")
        return principal.normalized_subject

    def identity_fingerprint(self, principal: AuthenticatedPrincipal) -> str:
        """Return the stable, store-secret-scoped pseudonym for one principal."""
        owner_id = self._require_principal(principal)
        fingerprint_key = hmac.new(
            self._identity_secret,
            b"cos-control-broker/identity-fingerprint/key/v1",
            hashlib.sha256,
        ).digest()
        return hmac.new(fingerprint_key, owner_id.encode("utf-8"), hashlib.sha256).hexdigest()

    def sender_acl_token(self, endpoint_id: str) -> str:
        """Return a non-secret compatibility token for an exact sender endpoint."""
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        return hmac.new(
            self._identity_secret,
            f"a2a-sender-endpoint-v1:{endpoint_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _uuid(value: Any, field: str) -> str:
        if not isinstance(value, str):
            raise ValueError(f"{field} must be a UUID")
        try:
            return str(UUID(value))
        except (ValueError, AttributeError):
            raise ValueError(f"{field} must be a UUID") from None

    @staticmethod
    def _fingerprint(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError("allowed sender fingerprints must be lowercase 64-character hex values")
        return value

    @classmethod
    def _fingerprint_list(cls, values: Any) -> list[str]:
        if values is None:
            return []
        if not isinstance(values, list) or len(values) > 100:
            raise ValueError("allowed_sender_fingerprints must be an array with at most 100 entries")
        normalized = [cls._fingerprint(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed_sender_fingerprints must not contain duplicates")
        return normalized

    @staticmethod
    def _correlation_id(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
            raise ValueError("correlation_id must be 1-128 safe ASCII characters")
        return value

    @staticmethod
    def _idempotency_key(value: Any) -> str:
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value):
            raise ValueError("idempotency_key must be 1-128 safe ASCII characters")
        return value

    @staticmethod
    def _owned_endpoint(
        db: sqlite3.Connection, endpoint_id: str, owner_id: str
    ) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM a2a_endpoints WHERE endpoint_id = ? AND owner_id = ?",
            (endpoint_id, owner_id),
        ).fetchone()
        if row is None:
            raise PermissionError("A2A endpoint not found")
        return row

    @staticmethod
    def _endpoint_summary(db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        fingerprint_count = db.execute(
            "SELECT COUNT(*) FROM a2a_endpoint_acl WHERE endpoint_id = ?", (row["endpoint_id"],)
        ).fetchone()[0]
        peer_count = db.execute(
            "SELECT COUNT(*) FROM a2a_endpoint_peer_acl WHERE endpoint_id = ?", (row["endpoint_id"],)
        ).fetchone()[0]
        peer_mode = db.execute(
            "SELECT 1 FROM a2a_endpoint_peer_acl_mode WHERE endpoint_id = ?", (row["endpoint_id"],)
        ).fetchone() is not None
        retired = row["state"] != "active"
        return {
            "endpoint_id": row["endpoint_id"],
            "label": row["label"],
            "state": row["state"],
            "identity_fingerprint": row["owner_fingerprint"],
            "acl_mode": "none" if retired else ("endpoint" if peer_mode else "identity_fingerprint"),
            "allowed_sender_count": 0 if retired else int(peer_count if peer_mode else fingerprint_count),
            "allowed_sender_endpoint_count": 0 if retired else int(peer_count),
            "allowed_sender_fingerprint_count": 0 if retired else int(fingerprint_count),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def register_endpoint(
        self,
        principal: AuthenticatedPrincipal,
        label: str,
        allowed_sender_fingerprints: Any = None,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        label = label.strip() if isinstance(label, str) else ""
        if not label or len(label) > 60:
            raise ValueError("label must contain 1-60 characters")
        allowed = self._fingerprint_list(allowed_sender_fingerprints)
        endpoint_id = str(uuid4())
        fingerprint = self.identity_fingerprint(principal)
        now = int(time.time())
        with self._transaction() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM a2a_endpoints WHERE owner_id = ? AND state = 'active'", (owner_id,)
            ).fetchone()[0]
            if count >= self.limits.max_endpoints_per_principal:
                raise ValueError(f"A2A endpoint limit reached ({self.limits.max_endpoints_per_principal})")
            db.execute(
                "INSERT INTO a2a_endpoints (endpoint_id, owner_id, owner_fingerprint, label, state, created_at, updated_at) VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (endpoint_id, owner_id, fingerprint, label, now, now),
            )
            db.executemany(
                "INSERT INTO a2a_endpoint_acl (endpoint_id, sender_fingerprint, created_at) VALUES (?, ?, ?)",
                [(endpoint_id, sender, now) for sender in allowed],
            )
            row = db.execute("SELECT * FROM a2a_endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
            return self._endpoint_summary(db, row)

    def retire_endpoint(
        self,
        principal: AuthenticatedPrincipal,
        endpoint_id: str,
        *,
        confirm_label: Any,
        confirm_delete: Any,
    ) -> dict[str, Any]:
        """Retire an endpoint, expire pending traffic, and preserve history."""
        owner_id = self._require_principal(principal)
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        if confirm_delete is not True:
            raise ValueError("confirm_delete must be true")
        if not isinstance(confirm_label, str) or not confirm_label:
            raise ValueError("confirm_label must exactly match the endpoint label")
        now = int(time.time())
        with self._transaction() as db:
            row = self._owned_endpoint(db, endpoint_id, owner_id)
            if confirm_label != row["label"]:
                raise ValueError("confirm_label does not match the endpoint label")
            if row["state"] == "disabled":
                return {
                    **self._endpoint_summary(db, row),
                    "acl_mode": "none",
                    "allowed_sender_count": 0,
                    "allowed_sender_endpoint_count": 0,
                    "allowed_sender_fingerprint_count": 0,
                    "retired": True,
                    "already_retired": True,
                    "expired_message_count": 0,
                    "history_retained": True,
                }
            cursor = db.execute(
                "UPDATE a2a_messages SET state = 'expired' "
                "WHERE (sender_endpoint_id = ? OR recipient_endpoint_id = ?) "
                "AND state IN ('queued', 'received')",
                (endpoint_id, endpoint_id),
            )
            expired_count = max(cursor.rowcount, 0)
            db.execute("DELETE FROM a2a_endpoint_acl WHERE endpoint_id = ?", (endpoint_id,))
            db.execute("DELETE FROM a2a_endpoint_peer_acl WHERE endpoint_id = ?", (endpoint_id,))
            db.execute(
                "UPDATE a2a_endpoints SET state = 'disabled', updated_at = ? WHERE endpoint_id = ?",
                (now, endpoint_id),
            )
            retired = db.execute("SELECT * FROM a2a_endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
            return {
                **self._endpoint_summary(db, retired),
                "acl_mode": "none",
                "allowed_sender_count": 0,
                "allowed_sender_endpoint_count": 0,
                "allowed_sender_fingerprint_count": 0,
                "retired": True,
                "already_retired": False,
                "expired_message_count": expired_count,
                "history_retained": True,
            }

    def set_endpoint_acl(
        self,
        principal: AuthenticatedPrincipal,
        endpoint_id: str,
        allowed_sender_fingerprints: Any,
    ) -> dict[str, Any]:
        """Replace legacy fingerprint ACL or switch to exact endpoint ACL tokens."""
        owner_id = self._require_principal(principal)
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        allowed = self._fingerprint_list(allowed_sender_fingerprints)
        now = int(time.time())
        with self._transaction() as db:
            row = self._owned_endpoint(db, endpoint_id, owner_id)
            if row["state"] != "active":
                raise ValueError("A2A endpoint is disabled")
            exact_mode = db.execute(
                "SELECT 1 FROM a2a_endpoint_peer_acl_mode WHERE endpoint_id = ?", (endpoint_id,)
            ).fetchone() is not None
            endpoint_rows = db.execute("SELECT endpoint_id, state FROM a2a_endpoints").fetchall()
            token_to_endpoint = {
                self.sender_acl_token(item["endpoint_id"]): item["endpoint_id"] for item in endpoint_rows
            }
            recognized_tokens = [value for value in allowed if value in token_to_endpoint]
            use_exact = not allowed or len(recognized_tokens) == len(allowed)
            if exact_mode and not use_exact:
                raise ValueError("Endpoint uses exact sender endpoint ACL; use sender_acl_token values from list_endpoints.peer_directory")
            if use_exact:
                sender_ids = [token_to_endpoint[value] for value in allowed]
                inactive = {item["endpoint_id"] for item in endpoint_rows if item["state"] != "active"}
                if any(sender_id in inactive for sender_id in sender_ids):
                    raise ValueError("Every allowed sender endpoint must be active")
                self._replace_peer_acl(db, endpoint_id, sender_ids, now)
                db.execute("DELETE FROM a2a_endpoint_acl WHERE endpoint_id = ?", (endpoint_id,))
                self._expire_disallowed_messages(db, endpoint_id, sender_ids)
            else:
                db.execute("DELETE FROM a2a_endpoint_acl WHERE endpoint_id = ?", (endpoint_id,))
                db.executemany(
                    "INSERT INTO a2a_endpoint_acl (endpoint_id, sender_fingerprint, created_at) VALUES (?, ?, ?)",
                    [(endpoint_id, sender, now) for sender in allowed],
                )
                self._expire_disallowed_fingerprints(db, endpoint_id, allowed)
            db.execute("UPDATE a2a_endpoints SET updated_at = ? WHERE endpoint_id = ?", (now, endpoint_id))
            updated = db.execute("SELECT * FROM a2a_endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
            return self._endpoint_summary(db, updated)

    @staticmethod
    def _replace_peer_acl(db: sqlite3.Connection, endpoint_id: str, sender_ids: list[str], now: int) -> None:
        db.execute(
            "INSERT INTO a2a_endpoint_peer_acl_mode (endpoint_id, configured_at) VALUES (?, ?) "
            "ON CONFLICT(endpoint_id) DO UPDATE SET configured_at = excluded.configured_at",
            (endpoint_id, now),
        )
        db.execute("DELETE FROM a2a_endpoint_peer_acl WHERE endpoint_id = ?", (endpoint_id,))
        db.executemany(
            "INSERT INTO a2a_endpoint_peer_acl (endpoint_id, sender_endpoint_id, created_at) VALUES (?, ?, ?)",
            [(endpoint_id, sender_id, now) for sender_id in sender_ids],
        )

    @staticmethod
    def _expire_disallowed_messages(db: sqlite3.Connection, recipient_id: str, allowed_sender_ids: list[str]) -> None:
        if allowed_sender_ids:
            placeholders = ",".join("?" for _ in allowed_sender_ids)
            db.execute(
                f"UPDATE a2a_messages SET state = 'expired' WHERE recipient_endpoint_id = ? "
                f"AND state IN ('queued', 'received') AND sender_endpoint_id NOT IN ({placeholders})",
                [recipient_id, *allowed_sender_ids],
            )
        else:
            db.execute(
                "UPDATE a2a_messages SET state = 'expired' WHERE recipient_endpoint_id = ? AND state IN ('queued', 'received')",
                (recipient_id,),
            )

    @staticmethod
    def _expire_disallowed_fingerprints(
        db: sqlite3.Connection, recipient_id: str, allowed_fingerprints: list[str]
    ) -> None:
        if allowed_fingerprints:
            placeholders = ",".join("?" for _ in allowed_fingerprints)
            db.execute(
                f"UPDATE a2a_messages SET state = 'expired' WHERE recipient_endpoint_id = ? "
                f"AND state IN ('queued', 'received') AND sender_fingerprint NOT IN ({placeholders})",
                [recipient_id, *allowed_fingerprints],
            )
        else:
            db.execute(
                "UPDATE a2a_messages SET state = 'expired' WHERE recipient_endpoint_id = ? AND state IN ('queued', 'received')",
                (recipient_id,),
            )

    def set_peer_acl(
        self,
        principal: AuthenticatedPrincipal,
        endpoint_id: str,
        allowed_sender_endpoint_ids: Any,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        if not isinstance(allowed_sender_endpoint_ids, list) or len(allowed_sender_endpoint_ids) > 100:
            raise ValueError("allowed_sender_endpoint_ids must be an array with at most 100 entries")
        allowed = [self._uuid(value, "allowed_sender_endpoint_ids item") for value in allowed_sender_endpoint_ids]
        if len(set(allowed)) != len(allowed):
            raise ValueError("allowed_sender_endpoint_ids must not contain duplicates")
        now = int(time.time())
        with self._transaction() as db:
            recipient = self._owned_endpoint(db, endpoint_id, owner_id)
            if recipient["state"] != "active":
                raise ValueError("A2A endpoint is disabled")
            if allowed:
                placeholders = ",".join("?" for _ in allowed)
                active_count = db.execute(
                    f"SELECT COUNT(*) FROM a2a_endpoints WHERE state = 'active' AND endpoint_id IN ({placeholders})",
                    allowed,
                ).fetchone()[0]
                if active_count != len(allowed):
                    raise ValueError("Every allowed sender endpoint must exist and be active")
            self._replace_peer_acl(db, endpoint_id, allowed, now)
            db.execute("DELETE FROM a2a_endpoint_acl WHERE endpoint_id = ?", (endpoint_id,))
            self._expire_disallowed_messages(db, endpoint_id, allowed)
            db.execute("UPDATE a2a_endpoints SET updated_at = ? WHERE endpoint_id = ?", (now, endpoint_id))
            updated = db.execute("SELECT * FROM a2a_endpoints WHERE endpoint_id = ?", (endpoint_id,)).fetchone()
            return self._endpoint_summary(db, updated)

    def list_endpoints(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM a2a_endpoints WHERE owner_id = ? ORDER BY created_at, endpoint_id", (owner_id,)
            ).fetchall()
            peers = db.execute(
                "SELECT * FROM a2a_endpoints WHERE state = 'active' ORDER BY label COLLATE NOCASE, created_at, endpoint_id"
            ).fetchall()
            own = [self._endpoint_summary(db, row) for row in rows]
            peer_directory = [
                {**self._endpoint_summary(db, row), "sender_acl_token": self.sender_acl_token(row["endpoint_id"])}
                for row in peers
            ]
            return {"endpoints": own, "peer_directory": peer_directory, "peer_directory_total": len(peer_directory)}

    def directory_list(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        """List active routing endpoints without exposing principal identifiers."""
        self._require_principal(principal)
        with self._connect() as db:
            rows = db.execute(
                "SELECT * FROM a2a_endpoints WHERE state = 'active' ORDER BY label COLLATE NOCASE, created_at, endpoint_id"
            ).fetchall()
            endpoints = [
                {**self._endpoint_summary(db, row), "sender_acl_token": self.sender_acl_token(row["endpoint_id"])}
                for row in rows
            ]
        return {
            "directory_scope": "authenticated_mailbox_database",
            "directory_version": 1,
            "endpoints": endpoints,
            "total": len(endpoints),
        }

    def _sender_is_allowed(
        self,
        db: sqlite3.Connection,
        recipient_endpoint_id: str,
        sender_endpoint_id: str,
        sender_fingerprint: str,
    ) -> bool:
        peer_mode = db.execute(
            "SELECT 1 FROM a2a_endpoint_peer_acl_mode WHERE endpoint_id = ?", (recipient_endpoint_id,)
        ).fetchone()
        if peer_mode is not None:
            return db.execute(
                "SELECT 1 FROM a2a_endpoint_peer_acl WHERE endpoint_id = ? AND sender_endpoint_id = ?",
                (recipient_endpoint_id, sender_endpoint_id),
            ).fetchone() is not None
        return db.execute(
            "SELECT 1 FROM a2a_endpoint_acl WHERE endpoint_id = ? AND sender_fingerprint = ?",
            (recipient_endpoint_id, sender_fingerprint),
        ).fetchone() is not None

    def _insert_message(
        self,
        db: sqlite3.Connection,
        principal: AuthenticatedPrincipal,
        sender_endpoint_id: str,
        recipient_endpoint_id: str,
        correlation_id: str,
        idempotency_key: str,
        message: str,
        ttl_seconds: int,
        reply_to_message_id: str | None = None,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        sender = self._owned_endpoint(db, sender_endpoint_id, owner_id)
        if sender["state"] != "active":
            raise ValueError("Sender endpoint is disabled")
        existing = db.execute(
            "SELECT * FROM a2a_messages WHERE sender_endpoint_id = ? AND idempotency_key = ?",
            (sender_endpoint_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            same_request = (
                existing["recipient_endpoint_id"] == recipient_endpoint_id
                and existing["correlation_id"] == correlation_id
                and existing["body"] == message
                and existing["reply_to_message_id"] == reply_to_message_id
            )
            if not same_request:
                raise ValueError("idempotency_key was already used with a different message")
            return {
                "message_id": existing["message_id"],
                "correlation_id": existing["correlation_id"],
                "state": existing["state"],
                "durable": True,
                "duplicate": True,
                "created_at": existing["created_at"],
                "expires_at": existing["expires_at"],
            }
        recipient = db.execute(
            "SELECT * FROM a2a_endpoints WHERE endpoint_id = ? AND state = 'active'", (recipient_endpoint_id,)
        ).fetchone()
        if recipient is None:
            raise PermissionError("Recipient A2A endpoint not found")
        if not self._sender_is_allowed(
            db, recipient_endpoint_id, sender_endpoint_id, sender["owner_fingerprint"]
        ):
            raise PermissionError("Sender endpoint is not authorized by recipient endpoint ACL")
        now = int(time.time())
        pending = db.execute(
            "SELECT COUNT(*) FROM a2a_messages WHERE recipient_endpoint_id = ? "
            "AND state IN ('queued', 'received') AND expires_at > ?",
            (recipient_endpoint_id, now),
        ).fetchone()[0]
        if pending >= self.limits.max_pending_per_endpoint:
            raise ValueError("Recipient A2A inbox is full")
        message_id = str(uuid4())
        expires_at = now + ttl_seconds
        db.execute(
            "INSERT INTO a2a_messages (message_id, correlation_id, sender_endpoint_id, sender_owner_id, sender_fingerprint, recipient_endpoint_id, body, state, idempotency_key, reply_to_message_id, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
            (
                message_id,
                correlation_id,
                sender_endpoint_id,
                owner_id,
                sender["owner_fingerprint"],
                recipient_endpoint_id,
                message,
                idempotency_key,
                reply_to_message_id,
                now,
                expires_at,
            ),
        )
        return {
            "message_id": message_id,
            "correlation_id": correlation_id,
            "state": "queued",
            "durable": True,
            "duplicate": False,
            "created_at": now,
            "expires_at": expires_at,
        }

    def _message_fields(
        self, idempotency_key: Any, message: Any, ttl_seconds: Any
    ) -> tuple[str, str, int]:
        idem = self._idempotency_key(idempotency_key)
        body = message.strip() if isinstance(message, str) else ""
        if not body or len(body) > 4000:
            raise ValueError("message must contain 1-4000 characters")
        ttl = self.limits.default_ttl_seconds if ttl_seconds is None else ttl_seconds
        if isinstance(ttl, bool) or not isinstance(ttl, int) or ttl < 60 or ttl > self.limits.max_ttl_seconds:
            raise ValueError(f"ttl_seconds must be between 60 and {self.limits.max_ttl_seconds}")
        return idem, body, ttl

    def send_message(
        self,
        principal: AuthenticatedPrincipal,
        sender_endpoint_id: str,
        recipient_endpoint_id: str,
        correlation_id: str,
        idempotency_key: str,
        message: str,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._require_principal(principal)
        sender = self._uuid(sender_endpoint_id, "sender_endpoint_id")
        correlation = self._correlation_id(correlation_id)
        idem, body, ttl = self._message_fields(idempotency_key, message, ttl_seconds)
        recipient = self._uuid(recipient_endpoint_id, "recipient_endpoint_id")
        with self._transaction() as db:
            now = int(time.time())
            db.execute(
                "UPDATE a2a_messages SET state = 'expired' WHERE state IN ('queued', 'received') AND expires_at <= ?",
                (now,),
            )
            return self._insert_message(db, principal, sender, recipient, correlation, idem, body, ttl)

    def read_inbox(
        self,
        principal: AuthenticatedPrincipal,
        endpoint_id: str,
        after_seq: int = 0,
        limit: int = 50,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        if isinstance(after_seq, bool) or not isinstance(after_seq, int) or after_seq < 0:
            raise ValueError("after_seq must be an integer >= 0")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        with self._transaction() as db:
            self._owned_endpoint(db, endpoint_id, owner_id)
            now = int(time.time())
            db.execute(
                "UPDATE a2a_messages SET state = 'expired' WHERE recipient_endpoint_id = ? "
                "AND state IN ('queued', 'received') AND expires_at <= ?",
                (endpoint_id, now),
            )
            rows = db.execute(
                "SELECT * FROM a2a_messages WHERE recipient_endpoint_id = ? AND seq > ? "
                "AND state IN ('queued', 'received') ORDER BY seq LIMIT ?",
                (endpoint_id, after_seq, limit),
            ).fetchall()
            seqs = [int(row["seq"]) for row in rows if row["state"] == "queued"]
            if seqs:
                placeholders = ",".join("?" for _ in seqs)
                db.execute(
                    f"UPDATE a2a_messages SET state = 'received', received_at = ? "
                    f"WHERE seq IN ({placeholders}) AND state = 'queued'",
                    [now, *seqs],
                )
            events = [
                {
                    "seq": int(row["seq"]),
                    "message_id": row["message_id"],
                    "correlation_id": row["correlation_id"],
                    "sender_endpoint_id": row["sender_endpoint_id"],
                    "sender_fingerprint": row["sender_fingerprint"],
                    "message": row["body"],
                    "reply_to_message_id": row["reply_to_message_id"],
                    "state": "received",
                    "created_at": row["created_at"],
                    "expires_at": row["expires_at"],
                }
                for row in rows
            ]
            return {
                "endpoint_id": endpoint_id,
                "events": events,
                "total": len(events),
                "next_after_seq": int(rows[-1]["seq"]) if rows else after_seq,
            }

    def ack_message(
        self,
        principal: AuthenticatedPrincipal,
        endpoint_id: str,
        message_id: str,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        endpoint_id = self._uuid(endpoint_id, "endpoint_id")
        message_id = self._uuid(message_id, "message_id")
        now = int(time.time())
        with self._transaction() as db:
            self._owned_endpoint(db, endpoint_id, owner_id)
            row = db.execute(
                "SELECT * FROM a2a_messages WHERE message_id = ? AND recipient_endpoint_id = ?",
                (message_id, endpoint_id),
            ).fetchone()
            if row is None:
                raise PermissionError("A2A message not found")
            if row["state"] == "expired" or row["expires_at"] <= now:
                db.execute("UPDATE a2a_messages SET state = 'expired' WHERE message_id = ?", (message_id,))
                return {"message_id": message_id, "state": "expired", "acked_at": None}
            if row["state"] != "acked":
                db.execute(
                    "UPDATE a2a_messages SET state = 'acked', acked_at = ? WHERE message_id = ?",
                    (now, message_id),
                )
            return {"message_id": message_id, "state": "acked", "acked_at": row["acked_at"] or now}

    def reply_message(
        self,
        principal: AuthenticatedPrincipal,
        sender_endpoint_id: str,
        original_message_id: str,
        idempotency_key: str,
        message: str,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        owner_id = self._require_principal(principal)
        sender = self._uuid(sender_endpoint_id, "sender_endpoint_id")
        idem, body, ttl = self._message_fields(idempotency_key, message, ttl_seconds)
        original_id = self._uuid(original_message_id, "original_message_id")
        expired = False
        result: dict[str, Any] | None = None
        with self._transaction() as db:
            original = db.execute(
                "SELECT * FROM a2a_messages WHERE message_id = ?", (original_id,)
            ).fetchone()
            if original is None or original["recipient_endpoint_id"] != sender:
                raise PermissionError("Original A2A message not found for this endpoint")
            sender_row = self._owned_endpoint(db, sender, owner_id)
            if sender_row["state"] != "active":
                raise ValueError("Sender endpoint is disabled")
            now = int(time.time())
            if original["expires_at"] <= now or original["state"] == "expired":
                db.execute("UPDATE a2a_messages SET state = 'expired' WHERE message_id = ?", (original_id,))
                expired = True
            else:
                existing_reply = db.execute(
                    "SELECT * FROM a2a_messages WHERE sender_endpoint_id = ? AND idempotency_key = ?",
                    (sender, idem),
                ).fetchone()
                if existing_reply is not None:
                    same_request = (
                        existing_reply["recipient_endpoint_id"] == original["sender_endpoint_id"]
                        and existing_reply["correlation_id"] == original["correlation_id"]
                        and existing_reply["body"] == body
                        and existing_reply["reply_to_message_id"] == original_id
                    )
                    if not same_request:
                        raise ValueError("idempotency_key was already used with a different message")
                    result = {
                        "message_id": existing_reply["message_id"],
                        "correlation_id": existing_reply["correlation_id"],
                        "state": existing_reply["state"],
                        "durable": True,
                        "duplicate": True,
                        "created_at": existing_reply["created_at"],
                        "expires_at": existing_reply["expires_at"],
                        "reply_to_message_id": original_id,
                        "original_message_acked": True,
                    }
                elif original["state"] == "acked":
                    raise ValueError("Original A2A message has already been acknowledged")
                else:
                    result = self._insert_message(
                        db,
                        principal,
                        sender,
                        original["sender_endpoint_id"],
                        original["correlation_id"],
                        idem,
                        body,
                        ttl,
                        original_id,
                    )
                    db.execute(
                        "UPDATE a2a_messages SET state = 'acked', acked_at = COALESCE(acked_at, ?) "
                        "WHERE message_id = ?",
                        (now, original_id),
                    )
                    result["reply_to_message_id"] = original_id
                    result["original_message_acked"] = True
        if expired:
            raise ValueError("Original A2A message has expired")
        assert result is not None
        return result
