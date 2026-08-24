"""Local user accounts.

Passwords are Argon2id hashes owned by the API. The database only stores the
encoded hash and account state; opaque browser sessions live in Redis so they
can be revoked immediately.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, Enum, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from pornarr_db.base import Base, TimestampMixin


class UserRole(StrEnum):
    ADMIN = "admin"
    USER = "user"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # The name the rest of the household sees on ratings, comments and sends.
    # Deliberately separate from `username`, which is a credential: a guest
    # joining a friend's server chooses how they appear without that choice
    # touching how they sign in. Null means "fall back to the username".
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda roles: [role.value for role in roles],
        ),
        nullable=False,
        default=UserRole.USER,
        server_default=UserRole.USER.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
