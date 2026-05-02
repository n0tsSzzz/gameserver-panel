from gamehost_api.core.security import generate_refresh_token, hash_refresh_token


def test_generate_refresh_token_is_urlsafe_and_unique() -> None:
    a = generate_refresh_token()
    b = generate_refresh_token()
    assert a != b
    assert len(a) >= 32
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_refresh_token_is_deterministic_sha256_hex() -> None:
    h = hash_refresh_token("token-abc")
    assert len(h) == 64
    assert h == hash_refresh_token("token-abc")
    assert h != hash_refresh_token("token-abd")
