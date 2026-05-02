from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from gamehost_api.api.v1.deps import get_current_user, get_session
from gamehost_api.core.config import get_settings
from gamehost_api.db.models import User
from gamehost_api.domain.auth import AuthService, TokenPair
from gamehost_api.domain.exceptions import RefreshInvalid
from gamehost_api.schemas.auth import AccessTokenOut, LoginIn, MeOut, RegisterIn

router = APIRouter()

_REFRESH_COOKIE = "gh_refresh"
_REFRESH_PATH = "/api/v1/auth"


def _set_refresh_cookie(response: Response, pair: TokenPair) -> None:
    s = get_settings()
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=pair.refresh_token,
        max_age=s.refresh_token_ttl_seconds,
        path=_REFRESH_PATH,
        domain=s.cookie_domain,
        secure=s.cookie_secure,
        httponly=True,
        samesite="lax",
    )


def _clear_refresh_cookie(response: Response) -> None:
    s = get_settings()
    response.delete_cookie(key=_REFRESH_COOKIE, path=_REFRESH_PATH, domain=s.cookie_domain)


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=MeOut)
async def register(payload: RegisterIn, session: AsyncSession = Depends(get_session)) -> User:
    return await AuthService(session).register(email=payload.email, password=payload.password)


@router.post("/login", response_model=AccessTokenOut)
async def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> AccessTokenOut:
    pair = await AuthService(session).login(
        email=payload.email,
        password=payload.password,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, pair)
    return AccessTokenOut(access_token=pair.access_token)


@router.post("/refresh", response_model=AccessTokenOut)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
    gh_refresh: str | None = Cookie(default=None),
) -> AccessTokenOut:
    if not gh_refresh:
        raise RefreshInvalid()
    pair = await AuthService(session).refresh(
        gh_refresh,
        user_agent=request.headers.get("user-agent"),
        ip=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, pair)
    return AccessTokenOut(access_token=pair.access_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    session: AsyncSession = Depends(get_session),
    gh_refresh: str | None = Cookie(default=None),
) -> Response:
    await AuthService(session).logout(gh_refresh)
    _clear_refresh_cookie(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=MeOut)
async def me(current: User = Depends(get_current_user)) -> User:
    return current
