"""B5 — inviting somebody onto this server, and their joining.

TWO AUDIENCES, TWO ROUTERS. Creating and listing invitations is administration
and is behind the admin role. Reading one and redeeming it must work for
somebody who has no account yet, so those two are unauthenticated — which is
precisely why they are the careful part of this file.

THE TOKEN IS SHOWN ONCE. It is returned by the call that creates it and never
again; only its hash is stored. An administrator who loses the link issues
another one, which is a smaller problem than a link recoverable from a backup.

WHAT AN UNREDEEMED TOKEN DISCLOSES. Deliberately almost nothing: whether it is
still valid, and nothing about the server, the household or who issued it. The
design's "You've been invited to Kai's library" would name the owner to anyone
holding a URL, including one found in a browser history on a shared machine.

CONSTANT-TIME IS NOT THE POINT HERE, UNIQUENESS IS. The lookup is by hash, so
there is nothing to compare byte by byte; a token is 32 bytes from
`secrets.token_urlsafe`, which is not guessable in the time an expiry allows.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from pornarr_api.auth import database_session, hash_password, require_role
from pornarr_db.audit import write_audit
from pornarr_db.models.automation import AutomationRule
from pornarr_db.models.invite import Invite
from pornarr_db.models.user import User, UserRole

admin_router = APIRouter(prefix="/admin/invites", tags=["admin"])
router = APIRouter(prefix="/invites", tags=["invites"])

Admin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
Session = Annotated[AsyncSession, Depends(database_session)]

# Long enough to send to somebody and have them get to it; short enough that a
# forgotten link stops working before anyone finds it.
DEFAULT_VALID_DAYS = 7
MAXIMUM_VALID_DAYS = 30

TOKEN_BYTES = 32


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class InviteCreate(BaseModel):
    valid_days: Annotated[int, Field(ge=1, le=MAXIMUM_VALID_DAYS)] = DEFAULT_VALID_DAYS
    note: Annotated[str | None, Field(max_length=128)] = None


class InviteCreated(BaseModel):
    id: UUID
    # Returned exactly once. There is no endpoint that will produce it again.
    token: str
    expires_at: datetime
    note: str | None


class InviteResponse(BaseModel):
    """What an administrator sees. Never the token."""

    id: UUID
    expires_at: datetime
    redeemed_at: datetime | None
    redeemed_username: str | None
    note: str | None


class InviteState(BaseModel):
    """What somebody holding a link is told before they commit to anything."""

    valid: bool
    # Absent when the invitation is not valid: an expiry is a fact about a
    # working link, and reporting one for a dead token invites guessing at the
    # window it had.
    expires_at: datetime | None


class InviteRedeem(BaseModel):
    username: Annotated[str, Field(min_length=1, max_length=64)]
    password: Annotated[SecretStr, Field(min_length=12)]
    display_name: Annotated[str | None, Field(max_length=64)] = None

    @field_validator("username")
    @classmethod
    def clean(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class RedeemedResponse(BaseModel):
    username: str
    display_name: str | None
    role: UserRole


def _utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes even for a timezone-aware column.

    The comparison below happens in Python rather than in SQL, so the value has
    to be given back the offset the column promised. Without this the unit
    suite — which runs on SQLite — raises on every expiry check while
    PostgreSQL is fine, which is the worst shape a bug can have.
    """
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _usable(invite: Invite, now: datetime) -> bool:
    return invite.redeemed_at is None and _utc(invite.expires_at) > now


@admin_router.post("", response_model=InviteCreated, status_code=201)
async def create_invite(payload: InviteCreate, admin: Admin, session: Session) -> InviteCreated:
    token = secrets.token_urlsafe(TOKEN_BYTES)
    invite = Invite(
        token_hash=token_hash(token),
        created_by=admin.id,
        expires_at=datetime.now(UTC) + timedelta(days=payload.valid_days),
        note=payload.note,
    )
    session.add(invite)
    await session.flush()
    write_audit(
        session,
        actor_id=admin.id,
        action="invite.created",
        target=str(invite.id),
        # The note, never the token. An audit log is a log.
        context={"note": payload.note, "valid_days": payload.valid_days},
    )
    return InviteCreated(id=invite.id, token=token, expires_at=invite.expires_at, note=invite.note)


@admin_router.get("", response_model=list[InviteResponse])
async def list_invites(_: Admin, session: Session) -> list[InviteResponse]:
    rows = (
        await session.execute(
            select(Invite, User.username)
            .outerjoin(User, User.id == Invite.redeemed_by)
            .order_by(Invite.created_at.desc())
        )
    ).tuples()
    return [
        InviteResponse(
            id=invite.id,
            expires_at=invite.expires_at,
            redeemed_at=invite.redeemed_at,
            redeemed_username=username,
            note=invite.note,
        )
        for invite, username in rows
    ]


@admin_router.delete("/{invite_id}", status_code=204)
async def revoke_invite(invite_id: UUID, admin: Admin, session: Session) -> None:
    """Withdraw an unused invitation.

    A redeemed one is left alone: the row is the record of who joined through
    which link, and deleting it would lose the fact somebody would want later.
    """
    invite = await session.get(Invite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404)
    if invite.redeemed_at is not None:
        raise HTTPException(status_code=409, detail="That invitation has already been used.")
    await session.delete(invite)
    write_audit(session, actor_id=admin.id, action="invite.revoked", target=str(invite_id))
    await session.flush()


@router.get("/{token}", response_model=InviteState)
async def invite_state(token: str, session: Session) -> InviteState:
    """Whether a link still works. Reachable without an account, by design.

    A 404 for a bad token and a 200 for a good one already distinguish the two;
    there is nothing further to hide by pretending otherwise, and returning 200
    for everything would make the join form unable to say "this link has
    expired" before somebody types a password into it.
    """
    invite = await session.scalar(select(Invite).where(Invite.token_hash == token_hash(token)))
    if invite is None or not _usable(invite, datetime.now(UTC)):
        return InviteState(valid=False, expires_at=None)
    return InviteState(valid=True, expires_at=invite.expires_at)


@router.post("/{token}/redeem", response_model=RedeemedResponse, status_code=201)
async def redeem_invite(token: str, payload: InviteRedeem, session: Session) -> RedeemedResponse:
    """Create the guest's account and spend the invitation.

    The row is locked for the duration. Two people opening the same link at the
    same moment would otherwise both pass the "not yet redeemed" check and both
    get an account from one invitation.
    """
    invite = await session.scalar(
        select(Invite).where(Invite.token_hash == token_hash(token)).with_for_update()
    )
    now = datetime.now(UTC)
    if invite is None or not _usable(invite, now):
        # One answer for expired, already used and never existed. Which of the
        # three it is tells a stranger something about links they do not hold.
        raise HTTPException(status_code=404, detail="That invitation is not usable.")

    taken = await session.scalar(select(User.id).where(User.username == payload.username))
    if taken is not None:
        raise HTTPException(status_code=409, detail="That name is taken.")

    guest = User(
        username=payload.username,
        password_hash=hash_password(payload.password.get_secret_value()),
        display_name=payload.display_name,
        # A guest, always. An invitation that could mint an administrator would
        # make a copied link a privilege escalation.
        role=UserRole.USER,
    )
    session.add(guest)
    await session.flush()
    session.add(AutomationRule(user_id=guest.id))

    invite.redeemed_at = now
    invite.redeemed_by = guest.id
    write_audit(
        session,
        actor_id=guest.id,
        action="invite.redeemed",
        target=str(invite.id),
        context={"username": guest.username},
    )
    await session.flush()
    return RedeemedResponse(
        username=guest.username, display_name=guest.display_name, role=guest.role
    )
