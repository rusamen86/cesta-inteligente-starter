from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .database import connection, initialize_database


class AuthorizationError(PermissionError):
    """Raised when authenticated channel context is not authorized for Cesta."""


@dataclass(frozen=True)
class TrustedRequestContext:
    agent_id: str
    channel: str
    group_jid: str
    session_key: str
    requester_sender_id: str
    sender_is_owner: bool
    received_at: datetime

    def to_dict(self) -> dict[str, object]:
        """Serialize the already trusted envelope for the local bridge protocol."""
        return {
            "agent_id": self.agent_id,
            "channel": self.channel,
            "group_jid": self.group_jid,
            "session_key": self.session_key,
            "requester_sender_id": self.requester_sender_id,
            "sender_is_owner": self.sender_is_owner,
            "received_at": self.received_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "TrustedRequestContext":
        received_at = datetime.fromisoformat(str(payload["received_at"]).replace("Z", "+00:00"))
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=timezone.utc)
        return cls(
            agent_id=str(payload["agent_id"]),
            channel=str(payload["channel"]),
            group_jid=str(payload["group_jid"]),
            session_key=str(payload["session_key"]),
            requester_sender_id=str(payload["requester_sender_id"]),
            sender_is_owner=bool(payload.get("sender_is_owner", False)),
            received_at=received_at,
        )


@dataclass(frozen=True)
class AuthorizedPrincipal:
    user_id: int
    name: str
    role: str
    requester_sender_id: str

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


class PermissionService:
    """Fail-closed authorization using exact, previously verified channel identities."""

    def __init__(self, db_path: str | Path, *, agent_id: str, group_jid: str) -> None:
        self.db_path = Path(db_path)
        self.agent_id = agent_id
        self.group_jid = group_jid

    def register_identity(
        self,
        *,
        name: str,
        role: str,
        channel: str,
        identity_kind: str,
        identity_value: str,
        verified_by_user_id: int | None = None,
    ) -> int:
        """Administrative bootstrap API. It is intentionally not exposed as a Cesta tool."""
        if role not in {"owner", "user"}:
            raise ValueError("role must be owner or user")
        if identity_kind not in {"e164", "jid", "lid", "requester_id"}:
            raise ValueError("unsupported identity kind")
        if not identity_value or identity_value != identity_value.strip():
            raise ValueError("identity_value must be non-empty and canonical")
        initialize_database(self.db_path)
        with connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, role FROM users WHERE household_id = 1 AND name = ?",
                (name,),
            ).fetchone()
            if row is None:
                cursor = conn.execute(
                    "INSERT INTO users(household_id, name, role, channel_identity) VALUES (1, ?, ?, ?)",
                    (name, role, identity_value),
                )
                user_id = int(cursor.lastrowid)
            else:
                user_id = int(row["id"])
                if row["role"] != role:
                    raise ValueError("existing user role does not match")
            conn.execute(
                """
                INSERT OR IGNORE INTO user_channel_identities(
                    user_id, channel, identity_kind, identity_value, verified_by
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, channel, identity_kind, identity_value, verified_by_user_id),
            )
            return user_id

    def authorize(self, context: TrustedRequestContext) -> AuthorizedPrincipal:
        if context.agent_id != self.agent_id:
            raise AuthorizationError("unexpected agent")
        if context.channel != "whatsapp":
            raise AuthorizationError("unexpected channel")
        if context.group_jid != self.group_jid:
            raise AuthorizationError("unexpected group")
        expected_session = f"agent:{self.agent_id}:whatsapp:group:{self.group_jid}"
        if context.session_key != expected_session:
            raise AuthorizationError("unexpected session")
        if not context.requester_sender_id:
            raise AuthorizationError("missing authenticated sender")

        initialize_database(self.db_path)
        with connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT u.id, u.name, u.role
                FROM user_channel_identities i
                JOIN users u ON u.id = i.user_id
                WHERE i.channel = 'whatsapp' AND i.identity_value = ?
                """,
                (context.requester_sender_id,),
            ).fetchone()
        if row is None:
            raise AuthorizationError("sender is not enrolled")

        principal = AuthorizedPrincipal(
            user_id=int(row["id"]),
            name=str(row["name"]),
            role=str(row["role"]),
            requester_sender_id=context.requester_sender_id,
        )
        # The runtime bit is supporting evidence only; it can never promote a user.
        if context.sender_is_owner and not principal.is_owner:
            raise AuthorizationError("owner bit does not match enrolled role")
        return principal
