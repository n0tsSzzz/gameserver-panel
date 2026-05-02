import time
import uuid

import pytest

from gamehost_api.core.security import create_access_token, decode_access_token


def test_decode_access_token_returns_claims() -> None:
    user_id = uuid.uuid4()
    token = create_access_token(user_id=user_id, email="a@b.test", role="user")
    claims = decode_access_token(token)
    assert claims["sub"] == str(user_id)
    assert claims["email"] == "a@b.test"
    assert claims["role"] == "user"
    assert claims["type"] == "access"


def test_decode_expired_access_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACCESS_TOKEN_TTL_SECONDS", "1")
    from gamehost_api.core.config import get_settings

    get_settings.cache_clear()
    token = create_access_token(user_id=uuid.uuid4(), email="x@y", role="user")
    time.sleep(2)
    with pytest.raises(ValueError):
        decode_access_token(token)
