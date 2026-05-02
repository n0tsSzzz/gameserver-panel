import uuid
from datetime import datetime
from typing import Annotated, Any

from email_validator import EmailNotValidError, validate_email
from pydantic import AfterValidator, Field

from gamehost_api.schemas.base import CamelModel


def _validate_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("email must be a string")
    try:
        info = validate_email(value, check_deliverability=False, test_environment=True)
    except EmailNotValidError as exc:
        raise ValueError(str(exc)) from exc
    return info.normalized


Email = Annotated[str, AfterValidator(_validate_email)]


class RegisterIn(CamelModel):
    email: Email
    password: str = Field(min_length=8, max_length=256)


class LoginIn(CamelModel):
    email: Email
    password: str = Field(min_length=1, max_length=256)


class AccessTokenOut(CamelModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(CamelModel):
    id: uuid.UUID
    email: str
    role: str
    is_active: bool
    created_at: datetime
