import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from gamehost_api.schemas.base import CamelModel


class RegisterIn(CamelModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginIn(CamelModel):
    email: EmailStr
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
