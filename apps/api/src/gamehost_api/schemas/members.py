import uuid
from datetime import datetime
from typing import Literal

from gamehost_api.schemas.auth import Email
from gamehost_shared.camel_model import CamelModel


class MemberInviteIn(CamelModel):
    email: Email
    role: Literal["viewer", "operator"]


class MemberInviteOut(CamelModel):
    invite_id: uuid.UUID
    token: str
    expires_at: datetime
    invite_url: str


class InvitePreviewOut(CamelModel):
    server_id: uuid.UUID
    server_name: str
    role: Literal["viewer", "operator"]
    email: str
    expires_at: datetime


class InviteAcceptOut(CamelModel):
    server_id: uuid.UUID
    role: Literal["viewer", "operator"]


class MemberOut(CamelModel):
    user_id: uuid.UUID
    email: str
    role: Literal["owner", "viewer", "operator"]
    invited_by: uuid.UUID | None
    created_at: datetime


class InviteOut(CamelModel):
    id: uuid.UUID
    email: str
    role: Literal["viewer", "operator"]
    expires_at: datetime
    created_at: datetime
