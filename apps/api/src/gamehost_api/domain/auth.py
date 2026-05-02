from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.core.config import get_settings
from gamehost_api.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from gamehost_api.db.models import User
from gamehost_api.domain.exceptions import (
    EmailAlreadyTaken,
    InvalidCredentials,
    RefreshInvalid,
    UserInactive,
)
from gamehost_api.repositories.refresh_tokens import RefreshTokenRepository
from gamehost_api.repositories.users import UserRepository


@dataclass(slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._users = UserRepository(session)
        self._refresh = RefreshTokenRepository(session)

    async def register(self, *, email: str, password: str) -> User:
        normalized = email.strip().lower()
        existing = await self._users.get_by_email(normalized)
        if existing is not None:
            raise EmailAlreadyTaken(normalized)
        try:
            user = await self._users.create(
                email=normalized, password_hash=hash_password(password), role="user"
            )
        except IntegrityError as exc:
            raise EmailAlreadyTaken(normalized) from exc
        return user

    async def login(
        self, *, email: str, password: str, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        user = await self._users.get_by_email(email)
        if user is None or not verify_password(user.password_hash, password):
            raise InvalidCredentials()
        if not user.is_active:
            raise UserInactive()
        return await self._issue_pair(user=user, user_agent=user_agent, ip=ip)

    async def refresh(
        self, refresh_token: str, *, user_agent: str | None, ip: str | None
    ) -> TokenPair:
        token_hash = hash_refresh_token(refresh_token)
        row = await self._refresh.get_by_token_hash(token_hash)
        now = datetime.now(UTC)
        if row is None or row.expires_at <= now:
            raise RefreshInvalid()
        if row.revoked_at is not None:
            await self._refresh.revoke_all_for_user(row.user_id)
            raise RefreshInvalid()
        user = await self._users.get(row.user_id)
        if user is None or not user.is_active:
            raise RefreshInvalid()
        await self._refresh.revoke(row)
        return await self._issue_pair(user=user, user_agent=user_agent, ip=ip)

    async def logout(self, refresh_token: str | None) -> None:
        if not refresh_token:
            return
        row = await self._refresh.get_by_token_hash(hash_refresh_token(refresh_token))
        if row is not None and row.revoked_at is None:
            await self._refresh.revoke(row)

    async def _issue_pair(self, *, user: User, user_agent: str | None, ip: str | None) -> TokenPair:
        s = get_settings()
        access = create_access_token(user_id=user.id, email=user.email, role=user.role)
        refresh = generate_refresh_token()
        expires = datetime.now(UTC) + timedelta(seconds=s.refresh_token_ttl_seconds)
        await self._refresh.create(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            expires_at=expires,
            user_agent=user_agent,
            ip=ip,
        )
        return TokenPair(access_token=access, refresh_token=refresh, refresh_expires_at=expires)
